"""后台会话（conversations/background.py）：worker 经内部端口替用户建会话、按回答方式发一轮、看状态、取消；
会话不进本人的「最近」和搜索；令牌要对、人要是空间成员、会话要是这个人的。业务服务进程里的扩展用 LocalBackground
建对话，判定一样，还能指定对话 id。只在扩展页面里用的回答方式（不进下拉）开的对话同样不进「最近」和搜索。"""

from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from knowwork_server.conversations.answer_modes import AnswerMode, AnswerModes
from knowwork_server.conversations.background import (
    AUDIENCE,
    BackgroundTurnError,
    BackgroundTurns,
    LocalBackground,
    background_signer,
    build_background_router,
)
from knowwork_server.conversations.listeners import Broadcaster
from knowwork_server.conversations.models import Conversation, RunEvent, RunSummary
from knowwork_server.conversations.pump import EventPump, PumpTimings
from knowwork_server.conversations.replay import EventFeed
from knowwork_server.conversations.service import ConversationService
from knowwork_server.conversations.writer import BatchWriter
from knowwork_server.db.base import in_workspace, make_engine, make_sessionmaker, make_workspace_sessionmaker
from knowwork_server.db.migrate import MAIN_LINE, upgrade_to_head
from knowwork_server.kernel.contract import KernelError
from knowwork_server.kernel.tokens import TokenSigner, derive_key

from .fakes import FakeKernel

pytestmark = pytest.mark.anyio
MASTER = "m" * 48
MEMBERS = {("u1", "ws_a"), ("u2", "ws_a")}
MODES = AnswerModes()
MODES.add(AnswerMode("page_only", "建模", "只在扩展页面的右栏里用", listed=False))


@pytest.fixture
async def world(app_db):
    """和生产一样：应用账号不是超级用户、表开了行级安全，接口得自己声明当前空间才写得进去。"""
    await upgrade_to_head(app_db.owner_url, app_role=app_db.role, branches=MAIN_LINE)
    engine = make_engine(app_db.app_url)
    sessionmaker = make_workspace_sessionmaker(engine)
    kernel = FakeKernel()
    writer = BatchWriter(sessionmaker)
    broadcaster = Broadcaster()
    pump = EventPump(kernel, sessionmaker, writer, broadcaster, timings=PumpTimings(idle_timeout=0.2))
    service = ConversationService(kernel, sessionmaker, pump, EventFeed(sessionmaker, writer, broadcaster,
                                                                         pump.produced_upto), answer_modes=MODES)
    app = FastAPI()
    app.include_router(build_background_router(service, background_signer(MASTER), make_sessionmaker(engine),
                                               member=lambda user, ws: (user, ws) in MEMBERS))
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://internal")
    local = LocalBackground(service, member=lambda user, ws: (user, ws) in MEMBERS)
    yield SimpleNamespace(kernel=kernel, service=service, http=http, sessionmaker=sessionmaker,
                          turns=BackgroundTurns("http://internal", background_signer(MASTER), http), local=local)
    await http.aclose()
    await pump.close()
    await engine.dispose()


async def test_create_send_state_and_cancel(world):
    kernel, turns, sessionmaker = world.kernel, world.turns, world.sessionmaker
    conversation_id = await turns.create("ws_a", "u1", title="评测：上个月净收入")
    assert (await turns.state("ws_a", "u1", conversation_id)).status == "pending"
    await turns.send("ws_a", "u1", conversation_id, "上个月净收入多少")
    assert kernel.sent[-1].text == "上个月净收入多少" and kernel.sent[-1].answer_mode == "agent"
    with in_workspace("ws_a"):
        async with sessionmaker() as session:
            session.add(RunSummary(conversation_id=conversation_id, turn=1, workspace_id="ws_a", user_id="u1",
                                   question="上个月净收入多少", status="running", started_at=datetime.now(UTC)))
            await session.commit()
    running = await turns.state("ws_a", "u1", conversation_id)
    assert running.status == "running" and running.asking and not running.ended, "宿主有待答的问题"
    with in_workspace("ws_a"):
        async with sessionmaker() as session:
            summary = await session.get(RunSummary, (conversation_id, 1))
            summary.status, summary.ended_at = "completed", datetime.now(UTC)
            session.add(RunEvent(conversation_id=conversation_id, seq=1, workspace_id="ws_a", host_epoch="e1",
                                 host_cursor=1, kind="session/event",
                                 data={"type": "assistant/message", "data": {"message": {"content": "共 3050 元"}}}))
            await session.commit()
    done = await turns.state("ws_a", "u1", conversation_id)
    assert (done.status, done.asking, done.answer, done.ended) == ("completed", False, "共 3050 元", True)
    await turns.cancel("ws_a", "u1", conversation_id)
    assert kernel.cancelled == [conversation_id]


async def test_background_conversations_stay_out_of_recents_and_search(world):
    service = world.service
    hidden = await world.turns.create("ws_a", "u1", title="评测：退款率")
    with in_workspace("ws_a"):
        shown = await service.create(workspace_id="ws_a", user_id="u1", title="退款率怎么算")
        async with world.sessionmaker() as session:
            assert (await session.get(Conversation, hidden)).background is True
        page = await service.list_page(workspace_id="ws_a", user_id="u1")
        assert [c.id for c in page.items] == [shown.id]
        hits = await service.search(workspace_id="ws_a", user_id="u1", query="退款率")
        assert [h.conversation.id for h in hits] == [shown.id]
        opened = await service.get(conversation_id=hidden, workspace_id="ws_a", user_id="u1")
    assert opened.id == hidden, "链接照样能打开"


async def test_page_only_answer_modes_keep_their_conversations_out_of_recents_and_search(world):
    """扩展页面右栏这类只在扩展页面里用的回答方式（listed=False）：开出来的对话不进「最近」和搜索（聊天页的下拉里没有它，
    在那里接着发会换成别的方式答），链接照样能打开；下拉里有的方式照常进。"""
    service = world.service
    with in_workspace("ws_a"):
        page_only = await service.create(workspace_id="ws_a", user_id="u1", title="诊断：退款率",
                                         answer_mode="page_only")
        chat = await service.create(workspace_id="ws_a", user_id="u1", title="退款率是多少", answer_mode="agent")
        page = await service.list_page(workspace_id="ws_a", user_id="u1")
        assert [c.id for c in page.items] == [chat.id]
        hits = await service.search(workspace_id="ws_a", user_id="u1", query="退款率")
        assert [h.conversation.id for h in hits] == [chat.id]
        opened = await service.get(conversation_id=page_only.id, workspace_id="ws_a", user_id="u1")
    assert opened.id == page_only.id, "链接照样能打开"


async def test_tokens_members_and_owners_are_checked(world):
    turns, http = world.turns, world.http
    other = BackgroundTurns("http://internal", TokenSigner(derive_key(MASTER, "host-callback")), http)
    with pytest.raises(BackgroundTurnError, match="401"):
        await other.create("ws_a", "u1"), "宿主回调的子密钥签的令牌不通用"
    forged = TokenSigner(derive_key("x" * 48, "background-turns")).issue(audience=AUDIENCE, workspace_id="ws_a",
                                                                         ttl_seconds=60)
    response = await http.post("/internal/background/conversations", json={"user_id": "u1"},
                               headers={"Authorization": f"Bearer {forged}"})
    assert response.status_code == 401
    with pytest.raises(BackgroundTurnError, match="403"):
        await turns.create("ws_a", "outsider")
    conversation_id = await turns.create("ws_a", "u1")
    with pytest.raises(BackgroundTurnError, match="404"):
        await turns.send("ws_a", "u2", conversation_id, "看看别人的会话")
    with pytest.raises(BackgroundTurnError, match="403"):
        await turns.send("ws_b", "u1", conversation_id, "令牌是别的空间的"), "u1 不是 ws_b 的成员"


async def test_unknown_answer_modes_are_refused(world):
    turns = world.turns
    conversation_id = await turns.create("ws_a", "u1")
    with pytest.raises(BackgroundTurnError, match="422"):
        await turns.send("ws_a", "u1", conversation_id, "随便问问", answer_mode="nope")


async def status_of(call) -> int | None:
    """本地入口的报错只写一句原因，状态码在 status 上。"""
    with pytest.raises(BackgroundTurnError) as caught:
        await call
    return caught.value.status


async def test_local_background_takes_a_chosen_id_and_checks_like_the_internal_port(world):
    """扩展先拿自己的 id 记下东西再建对话：指定 id 建；已有同 id 的拒绝；id 不合规、不是成员都拒绝；宿主连不上报 502；
    不藏起来的进「最近」。"""
    local, kernel = world.local, world.kernel
    assert await local.create("ws_a", "u1", title="经营分析", conversation_id="exc_1") == "exc_1"
    assert kernel.opened[-1].session_id == "exc_1"
    assert await status_of(local.create("ws_a", "u1", conversation_id="exc_1")) == 409
    assert await status_of(local.create("ws_a", "u1", conversation_id="带空格 的 id")) == 422
    assert await status_of(local.create("ws_a", "outsider")) == 403
    world.kernel.open_effects.append(KernelError("unreachable", "宿主没起来", 503))
    assert await status_of(local.create("ws_a", "u1", conversation_id="exc_3")) == 502, "宿主连不上同公开端口建对话"
    await local.create("ws_a", "u1", conversation_id="exc_2", background=False)
    with in_workspace("ws_a"):
        async with world.sessionmaker() as session:
            assert (await session.get(Conversation, "exc_1")).background is True
        page = await world.service.list_page(workspace_id="ws_a", user_id="u1")
    assert [c.id for c in page.items] == ["exc_2"], "不藏起来的进本人的「最近」"
