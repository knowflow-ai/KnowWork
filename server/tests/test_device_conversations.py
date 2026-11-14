"""在用户设备上执行的对话（原生应用的本机宿主跑智能体，例如桌面端的本机会话）：

它是一条普通对话，列表、历史、事件流、改名、删除照旧；只是智能体不在我们的宿主上跑——建的时候不开宿主会话，
事件由设备送进来（经同一条管道编号、投影、落库、推送），业务服务不接它的发消息、停止、审批、回答，交给那台设备。
"""

import hashlib
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

import knowwork_server.approvals.models  # noqa: F401  注册表结构
from knowwork_server.access.models import Workspace
from knowwork_server.approvals.grants import SessionGrants
from knowwork_server.conversations.api import SendMessage, build_router
from knowwork_server.conversations.device import (
    DEVICE_SOURCE,
    DeviceConversations,
    InvalidEvents,
    NotOnDevice,
    Position,
)
from knowwork_server.conversations.listeners import Broadcaster
from knowwork_server.conversations.models import RunEvent
from knowwork_server.conversations.pump import EventPump
from knowwork_server.conversations.replay import Cursor, EventFeed
from knowwork_server.conversations.scopes import ConversationScope, ConversationScopes, ScopeClaim
from knowwork_server.conversations.service import ConversationService, RunsOnDevice
from knowwork_server.conversations.turn_context import TurnContextHook
from knowwork_server.conversations.writer import BatchWriter
from knowwork_server.kernel.contract import (
    HostModel,
    KnowledgeCatalog,
    KnowledgeEntry,
    QuestionAnswer,
    QuestionAnswerItem,
    SessionPolicy,
    TurnContext,
)

from .fakes import FakeKernel, token_actor

pytestmark = pytest.mark.anyio

CONV = "/api/workspaces/default/conversations"
ALICE = "a" * 48
OWNER = "device-owner"


@pytest.fixture
async def world(sessionmaker):
    kernel = FakeKernel()
    writer, broadcaster = BatchWriter(sessionmaker), Broadcaster()
    writer.start()
    pump = EventPump(kernel, sessionmaker, writer, broadcaster)
    feed = EventFeed(sessionmaker, writer, broadcaster, pump.produced_upto)
    service = ConversationService(kernel, sessionmaker, pump, feed)
    app = FastAPI()
    app.include_router(build_router(service, token_actor(ALICE, user_id=OWNER)))
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://kw",
                               headers={"authorization": f"Bearer {ALICE}"})
    device = DeviceConversations(service, pump)
    yield kernel, service, device, client, pump, writer
    await client.aclose()
    await pump.close()
    await writer.close()


def raw(seq, type_, data=None):
    return {"type": type_, "seq": seq, "time": 1000 + seq, "data": data or {}}


async def test_created_without_a_host_session_and_listed_like_any_other(world):
    kernel, _, device, client, *_ = world
    conversation = await device.create(workspace_id="default", user_id=OWNER, title="整理报销单")
    assert conversation.source == DEVICE_SOURCE and conversation.id.startswith("c_")
    assert kernel.opened == [], "智能体在设备上跑，不开我们宿主上的会话"
    listed = {c["id"]: c["source"] for c in (await client.get(CONV)).json()["items"]}
    assert (await client.get(f"{CONV}/{conversation.id}")).json()["source"] == DEVICE_SOURCE
    assert (await client.get(f"{CONV}/c_nope")).status_code == 404
    assert listed[conversation.id] == DEVICE_SOURCE


async def test_events_from_the_device_become_the_conversation_history(world, sessionmaker):
    kernel, service, device, _, pump, writer = world
    conversation = await device.create(workspace_id="default", user_id=OWNER)
    assert await device.position(conversation) == Position(seq=None, children={})
    events = [raw(0, "turn/start", {"turn": 1}),
              raw(1, "user/message", {"id": "u1", "source": {"kind": "user"},
                                      "content": [{"type": "text", "text": "整理一下"}]}),
              raw(2, "turn/end", {"turn": 1, "reason": {"kind": "completed"}})]
    assert await device.push(conversation, events) == 2
    assert await device.push(conversation, events[2:]) == 2, "传重了不存两份"
    assert await device.position(conversation) == Position(seq=2, children={})
    service.events(conversation, Cursor(0))
    assert kernel.subscribe_calls == [] and not pump.is_active(conversation.id), "打开事件流也不去宿主拉"
    await writer.flush()
    async with sessionmaker() as session:
        rows = (await session.execute(select(RunEvent).where(RunEvent.conversation_id == conversation.id)
                                      .order_by(RunEvent.seq))).scalars().all()
    assert [(r.seq, r.host_cursor, r.kind) for r in rows] == [(1, 0, "session/event"), (2, 1, "session/event"),
                                                               (3, 2, "session/event")]
    assert [u["type"] for r in rows for u in r.ui] == ["run.started", "item.completed", "run.completed"]


@pytest.mark.parametrize("bad", [[{"type": "turn/start"}], ["x"], [raw(-1, "turn/start")], [raw(0, "")],
                                 [raw(i, "x/y") for i in range(501)]])
async def test_malformed_events_are_refused(world, bad):
    _, _, device, *_ = world
    conversation = await device.create(workspace_id="default", user_id=OWNER)
    with pytest.raises(InvalidEvents):
        await device.push(conversation, bad)


async def test_only_the_device_can_send_stop_or_decide(world):
    kernel, service, device, client, *_ = world
    conversation = await device.create(workspace_id="default", user_id=OWNER)
    path = f"{CONV}/{conversation.id}"
    for response in (await client.post(f"{path}/messages", json={"text": "hi"}),
                     await client.post(f"{path}/cancel"),
                     await client.post(f"{path}/approvals/ap1", json={"allow": True}),
                     await client.post(f"{path}/questions/q1", json={"answers": [{"id": "x", "selected": ["好"]}]})):
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "runs_on_device"
    assert (await client.get(f"{path}/approvals")).json() == []
    assert (await client.get(f"{path}/questions")).json() == []
    assert (await client.post(f"{path}/warm")).status_code == 204
    assert kernel.sent == [] and kernel.cancelled == [] and kernel.opened == []
    with pytest.raises(RunsOnDevice):
        await service.activate(conversation)


class NoHost:
    """碰一下就报错的内核：设备上执行的对话在宿主上没有会话，业务服务的任何操作都不该去碰宿主。"""

    def __getattr__(self, name):
        raise AssertionError(f"设备上执行的对话碰了宿主：{name}")


async def test_no_service_operation_reaches_the_host_for_a_device_conversation(world):
    """服务里每个公开操作对设备上的对话都过一遍：要宿主做的（发消息、停止、审批、回答、激活）拒绝，没东西可做的（待审批与
    待回答、预热、跟事件、释放）直接返回，改名照常——都不碰宿主。新加碰宿主的操作经 _host() 拿内核，这里会替它把关。"""
    _, service, device, *_ = world
    conversation = await device.create(workspace_id="default", user_id=OWNER)
    service._kernel = NoHost()
    for refused in (lambda: service.send(conversation, sent(text="hi").turn()),
                    lambda: service.cancel(conversation),
                    lambda: service.decide(conversation, "ap1", True, remember=True),
                    lambda: service.answer(conversation, "q1", QuestionAnswer(answers=(QuestionAnswerItem(id="x", selected=("好",)),))),
                    lambda: service.activate(conversation)):
        with pytest.raises(RunsOnDevice):
            await refused()
    assert await service.approvals(conversation) == []
    assert await service.questions(conversation) == []
    await service.warm(conversation)
    service.events(conversation, Cursor())
    renamed = await service.rename(conversation, "新名字")
    assert renamed.title == "新名字"
    await service.delete(conversation)


async def test_rename_and_delete_work_without_the_host(world):
    kernel, _, device, client, *_ = world
    conversation = await device.create(workspace_id="default", user_id=OWNER)
    await device.push(conversation, [raw(0, "turn/start", {"turn": 1})])
    path = f"{CONV}/{conversation.id}"
    assert (await client.patch(path, json={"title": "新名字"})).json()["title"] == "新名字"
    assert (await client.delete(path)).status_code == 204
    assert kernel.disposed == []
    assert conversation.id not in {c["id"] for c in (await client.get(CONV)).json()["items"]}


async def test_owned_finds_only_the_users_own_device_conversations(world):
    _, service, device, *_ = world
    mine = await device.create(workspace_id="default", user_id=OWNER)
    on_server = await service.create(workspace_id="default", user_id=OWNER)
    assert (await device.owned(workspace_id="default", user_id=OWNER, conversation_id=mine.id)).id == mine.id
    assert await device.owned(workspace_id="default", user_id="bob", conversation_id=mine.id) is None
    assert await device.owned(workspace_id="default", user_id=OWNER, conversation_id=on_server.id) is None
    assert await device.owned(workspace_id="default", user_id=OWNER, conversation_id="c_nope") is None


async def test_delete_takes_the_conversation_back(world):
    _, _, device, *_ = world
    conversation = await device.create(workspace_id="default", user_id=OWNER)
    await device.delete(conversation)
    assert await device.owned(workspace_id="default", user_id=OWNER, conversation_id=conversation.id) is None


async def test_sub_agent_sessions_are_pushed_alongside_with_their_own_numbering(world, sessionmaker):
    """子 Agent 的事件在它自己的会话里、序号另起：设备带上子会话编号送来，收成和我们宿主转发子 Agent 事件同一种帧。"""
    _, _, device, _, _, writer = world
    conversation = await device.create(workspace_id="default", user_id=OWNER)
    await device.push(conversation, [raw(0, "turn/start", {"turn": 1}), raw(1, "subagent/catalog", {"childId": "kid-1"})])
    assert await device.push(conversation, [raw(0, "turn/start", {"turn": 1}), raw(1, "tool/call")], session="kid-1") == 1
    assert await device.push(conversation, [raw(1, "tool/call")], session="kid-1") == 1
    assert await device.position(conversation) == Position(seq=1, children={"kid-1": 1})
    await writer.flush()
    async with sessionmaker() as session:
        rows = (await session.execute(select(RunEvent).where(RunEvent.conversation_id == conversation.id)
                                      .order_by(RunEvent.seq))).scalars().all()
    assert [(r.kind, r.host_cursor) for r in rows] == [("session/event", 0), ("session/event", 1),
                                                        ("subagent/event", 0), ("subagent/event", 1)]
    assert rows[2].data == {"sessionId": "kid-1", "event": raw(0, "turn/start", {"turn": 1})}
    assert rows[2].ui == [], "子 Agent 自己的一轮不算主对话的一轮"


@pytest.mark.parametrize("bad", ["", "a b", "x" * 57, "有中文"])
async def test_malformed_sub_session_ids_are_refused(world, bad):
    _, _, device, *_ = world
    conversation = await device.create(workspace_id="default", user_id=OWNER)
    with pytest.raises(InvalidEvents):
        await device.push(conversation, [raw(0, "turn/start")], session=bad)


async def test_live_text_from_the_device_reaches_open_pages_without_being_stored(world, sessionmaker):
    """模型正在写的字：设备送来的片段只推给正在看的页面，不落库；这一轮结束后不再给新打开的页面补半截。"""
    _, service, device, _, pump, writer = world
    conversation = await device.create(workspace_id="default", user_id=OWNER)
    await device.push(conversation, [raw(0, "turn/start", {"turn": 1})])
    feed = service.events(conversation, Cursor(0))
    assert (await anext(feed)).type != "live"  # 先回放已有的那一条
    await device.push_live(conversation, [{"type": "start", "attemptId": "a1", "turn": 1, "step": 1},
                                           {"type": "text", "attemptId": "a1", "text": "正在"},
                                           {"type": "text", "attemptId": "a1", "text": "写"}, {"type": "看不懂"}])
    got = [await anext(feed) for _ in range(3)]
    assert [item.type for item in got] == ["live", "live", "live"] and all(item.sse_id is None for item in got)
    assert [item.payload["text"] for item in got] == ["", "正在", "写"]
    assert pump.live_snapshot(conversation.id)["text"] == "正在写", "中途打开的页面先补到目前为止的全文"
    await device.push(conversation, [raw(1, "turn/end", {"turn": 1, "reason": {"kind": "cancelled"}})])
    assert pump.live_snapshot(conversation.id) is None
    await feed.aclose()
    await writer.flush()
    async with sessionmaker() as session:
        kinds = (await session.execute(select(RunEvent.kind).where(RunEvent.conversation_id == conversation.id))).scalars().all()
    assert kinds == ["session/event", "session/event"]
    with pytest.raises(InvalidEvents):
        await device.push_live(conversation, ["x"])


class RecordingProfiles:
    """运行配置的替身：记下每轮编译用的选择与绑定。版本号按选择算，选择没变就不重新绑定。"""

    def __init__(self) -> None:
        self.compiled: list = []
        self.bindings: dict[str, object] = {}

    async def compile(self, conversation, selection=None):
        self.compiled.append(selection)
        return SimpleNamespace(version=repr(selection), selection=selection,
                               policy=lambda: SessionPolicy(deny=("knowwork__web_search",)))

    async def bound(self, conversation):
        return self.bindings.get(conversation.id)

    async def bind(self, conversation, profile):
        self.bindings[conversation.id] = profile


async def catalog_of(conversation, profile):
    return KnowledgeCatalog(bases=(KnowledgeEntry(id="builtin.kb1", name="制度库", description="公司制度"),))


@pytest.fixture
async def profiled(sessionmaker):
    kernel = FakeKernel()
    writer, broadcaster = BatchWriter(sessionmaker), Broadcaster()
    writer.start()
    pump = EventPump(kernel, sessionmaker, writer, broadcaster)
    feed = EventFeed(sessionmaker, writer, broadcaster, pump.produced_upto)
    profiles = RecordingProfiles()
    service = ConversationService(kernel, sessionmaker, pump, feed, profiles=profiles, knowledge_catalog=catalog_of)
    yield service, DeviceConversations(service, pump), profiles, kernel, writer
    await pump.close()
    await writer.close()


def sent(**body) -> SendMessage:
    """页面发消息的请求（云端发消息与设备开始一轮收的是同一个）。"""
    return SendMessage.model_validate(body)


async def test_a_device_turn_is_prepared_like_a_cloud_send(profiled):
    """设备那边发一轮前先来这里：和云端发消息同一套准备（@ 核对、本轮选择编译并绑定、知识库目录），
    交回的是我们宿主收的那两份请求体，设备原样交给本机宿主的收件逻辑。"""
    _, device, profiles, kernel, _ = profiled
    conversation = await device.create(workspace_id="default", user_id=OWNER)
    request = sent(text="报销标准是什么", time_zone="Asia/Shanghai", dropped_conditions=["部门 = 财务部"],
                   mentions=[{"type": "knowledge_base", "id": "builtin.kb1", "name": "制度库"},
                             {"type": "document", "id": "builtin.kb2/d1", "name": "差旅办法", "base": "builtin.kb2",
                              "base_name": "财务库"}])
    prepared = await device.prepare_turn(conversation, request.turn(), request.selection())
    assert prepared.turn == {
        "text": "报销标准是什么", "mode": "followup", "language": "zh", "answerMode": "agent", "timeZone": "Asia/Shanghai",
        "knowledge": {"bases": [{"id": "builtin.kb1", "name": "制度库", "description": "公司制度"}], "omitted": 0},
        "mentions": [{"type": "knowledge_base", "id": "builtin.kb1", "name": "制度库"},
                     {"type": "document", "id": "builtin.kb2/d1", "name": "差旅办法", "base": "builtin.kb2",
                      "baseName": "财务库"}]}
    assert prepared.session == {"sessionId": conversation.id, "policy": {"deny": ["knowwork__web_search"]},
                                "resume": False, "owner": {"workspaceId": "default", "userId": OWNER}}, \
        "令牌不在里面：设备按对话自己换"
    chosen = profiles.compiled[-1]
    assert chosen.knowledge_bases == ("builtin.kb1",) and chosen.dropped_conditions == ("部门 = 财务部",)
    assert [(p.base, p.documents) for p in chosen.pins] == [("builtin.kb2", ("builtin.kb2/d1",))]
    assert profiles.bindings[conversation.id].selection == chosen
    assert kernel.opened == [] and kernel.sent == [], "不送给我们的宿主"
    # 下一轮没有 @：范围回到全部可查的（和云端每轮重新编译一样）
    again = await device.prepare_turn(conversation, sent(text="还有呢").turn(), sent(text="还有呢").selection())
    assert "mentions" not in again.turn
    assert profiles.bindings[conversation.id].selection.knowledge_bases is None


async def test_a_steer_goes_into_the_running_turn_without_touching_the_session(profiled):
    _, device, profiles, *_ = profiled
    conversation = await device.create(workspace_id="default", user_id=OWNER)
    first = sent(text="整理一下", mentions=[{"type": "knowledge_base", "id": "builtin.kb1", "name": "制度库"}])
    await device.prepare_turn(conversation, first.turn(), first.selection())
    bound = profiles.bindings[conversation.id]
    steer = sent(text="只看今年的", mode="steer", mentions=[{"type": "knowledge_base", "id": "builtin.kb9", "name": "别的库"}])
    prepared = await device.prepare_turn(conversation, steer.turn(), steer.selection())
    assert prepared.session is None and prepared.turn["mode"] == "steer" and "mentions" not in prepared.turn
    assert profiles.bindings[conversation.id] is bound, "新选择留到下一轮"


async def test_the_device_session_resumes_once_the_server_has_seen_a_turn(profiled):
    """开会话的合同：业务服务收到过回合的标 resume，设备上找不到会话记录时报错，不悄悄新开（同云端恢复打开）。"""
    _, device, _, _, writer = profiled
    conversation = await device.create(workspace_id="default", user_id=OWNER)
    assert (await device.session(conversation))["resume"] is False
    await device.push(conversation, [raw(0, "turn/start", {"turn": 1})])
    await writer.flush()
    assert (await device.session(conversation))["resume"] is True


async def test_the_device_takes_the_model_list_from_the_same_source(sessionmaker):
    """设备上的本机宿主说不认识输入框选的模型时，设备来取清单：和推给我们宿主的同一份（没配模型源时是空的）。"""
    kernel = FakeKernel()
    writer, broadcaster = BatchWriter(sessionmaker), Broadcaster()
    pump = EventPump(kernel, sessionmaker, writer, broadcaster)
    service = ConversationService(kernel, sessionmaker, pump, EventFeed(sessionmaker, writer, broadcaster, pump.produced_upto))

    async def listed():
        return (HostModel(id="fast", name="快"),)

    assert await DeviceConversations(service, pump, models=listed).models() == {
        "models": [{"id": "fast", "name": "快", "vision": False}]}
    assert await DeviceConversations(service, pump).models() == {"models": []}
    await pump.close()


async def test_a_device_conversation_with_an_expert_is_prepared_by_its_scope(sessionmaker):
    """本机会话挂上专家（建对话时 ScopeClaim）以后，每一轮照云端按专家来：回答方式固定成专家的，身份随这一轮的合同交给本机宿主。"""
    scopes, hook, bound = ConversationScopes(), TurnContextHook(), set()

    async def scope_of(workspace_id, conversation_id):
        return ConversationScope("经营分析", answer_mode="quick") if conversation_id in bound else None

    class Claim:
        async def claim(self, workspace_id, conversation_id, user_id, item_id):
            bound.add(conversation_id)

        async def release(self, workspace_id, conversation_id):
            bound.discard(conversation_id)

    async def persona(request):
        return TurnContext(persona="你是专家「经营分析」。") if request.conversation_id in bound else None

    scopes.add(scope_of)
    scopes.add_claimer("expert", Claim())
    hook.add(persona)
    kernel = FakeKernel()
    writer, broadcaster = BatchWriter(sessionmaker), Broadcaster()
    pump = EventPump(kernel, sessionmaker, writer, broadcaster)
    service = ConversationService(kernel, sessionmaker, pump, EventFeed(sessionmaker, writer, broadcaster, pump.produced_upto),
                                  scopes=scopes, turn_context=hook)
    device = DeviceConversations(service, pump)
    conversation = await device.create(workspace_id="default", user_id=OWNER, scope=ScopeClaim("expert", "ex_1"))
    request = sent(text="上月营收", answer_mode="agent")
    prepared = await device.prepare_turn(conversation, request.turn(), request.selection())
    assert prepared.turn["answerMode"] == "quick", "回答方式固定成专家的，输入框里选的不算"
    assert prepared.turn["context"]["persona"] == "你是专家「经营分析」。"
    await device.delete(conversation)
    assert bound == set(), "删了对话，挂着的专家一起撤掉"
    await pump.close()


async def test_only_device_conversations_are_prepared_here(profiled):
    _, device, profiles, *_ = profiled
    cloud = SimpleNamespace(id="c_cloud", source="", workspace_id="default", user_id=OWNER)
    with pytest.raises(NotOnDevice):
        await device.prepare_turn(cloud, sent(text="hi").turn())
    with pytest.raises(NotOnDevice):
        await device.session(cloud)
    assert profiles.compiled == [], "不在设备上执行的不碰它的运行配置"


async def test_a_device_remembers_an_approval_the_host_answerer_then_finds(profiled, sessionmaker):
    """本机会话勾了「本会话内同类操作不再询问」：按对话绑定的运行配置记，宿主的审批应答者查授权时查得到（同云端那一条）。"""
    service, _, profiles, *_ = profiled
    async with sessionmaker() as session, session.begin():  # 授权表按空间有外键
        await session.execute(insert(Workspace).values(id="default", name="默认空间").on_conflict_do_nothing())
    grants = SessionGrants(sessionmaker, lambda ref: _version(profiles, ref))
    device = DeviceConversations(service, service._pump, grants=grants)
    conversation = await device.create(workspace_id="default", user_id=OWNER)
    assert await device.remember_approval(conversation, "bash") is False, "还没绑定运行配置时不记，下次照常问"
    await device.session(conversation)
    assert await device.remember_approval(conversation, "bash") is True
    assert await grants.covers("default", conversation.id, OWNER, "bash") is True
    assert await grants.covers("default", conversation.id, OWNER, "write_file") is False
    with pytest.raises(ValueError):
        await device.remember_approval(conversation, "rm -rf")
    cloud = SimpleNamespace(id="c_cloud", source="", workspace_id="default", user_id=OWNER)
    with pytest.raises(NotOnDevice):
        await device.remember_approval(cloud, "bash")


async def _version(profiles, ref):
    """绑定的运行配置版本（替身的版本号是选择的 repr，太长，取个摘要；真实的是短的版本号）。"""
    bound = profiles.bindings.get(ref.id)
    return None if bound is None else hashlib.sha256(bound.version.encode()).hexdigest()[:16]
