"""会话接口带上本轮选择；运行配置取不到时 503，且不外泄原因。"""

import httpx
import pytest
from fastapi import FastAPI

from knowwork_server.conversations.api import build_router
from knowwork_server.conversations.listeners import Broadcaster
from knowwork_server.conversations.pump import EventPump
from knowwork_server.conversations.replay import EventFeed
from knowwork_server.conversations.service import AttachmentUnavailable, ConversationService
from knowwork_server.conversations.writer import BatchWriter
from knowwork_server.kernel.contract import TurnAttachment
from knowwork_server.profile.compiler import ProfileFacts, compile_profile
from knowwork_server.profile.errors import ProfileUnavailable

from .fakes import FakeKernel, token_actor

CONV = "/api/workspaces/default/conversations"

pytestmark = pytest.mark.anyio
TOKEN = "d" * 48
AUTH = {"authorization": f"Bearer {TOKEN}"}


class Profiles:
    def __init__(self):
        self.selections, self.fail, self.bound = [], False, {}

    async def compile(self, conversation, selection=None):
        if self.fail:
            raise ProfileUnavailable()
        self.selections.append(selection)
        return compile_profile(ProfileFacts(workspace_id=conversation.workspace_id, user_id=conversation.user_id,
                                            role="editor", member=True, selection=selection))

    async def bound_version(self, conversation):
        return self.bound.get(conversation.id)

    async def bind(self, conversation, profile):
        self.bound[conversation.id] = profile.version


def client(sessionmaker, profiles, attachments=None):
    kernel = FakeKernel()
    writer, broadcaster = BatchWriter(sessionmaker), Broadcaster()
    pump = EventPump(kernel, sessionmaker, writer, broadcaster)
    service = ConversationService(kernel, sessionmaker, pump, EventFeed(sessionmaker, writer, broadcaster,
                                                                        pump.produced_upto), profiles=profiles,
                                  attachments=attachments)
    app = FastAPI()
    app.include_router(build_router(service, token_actor(TOKEN)))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://public"), pump, kernel


async def test_selection_reaches_compiler(sessionmaker):
    profiles = Profiles()
    http, pump, kernel = client(sessionmaker, profiles)
    async with http:
        created = (await http.post(CONV, json={}, headers=AUTH)).json()
        sent = await http.post(f"{CONV}/{created['id']}/messages", headers=AUTH,
                               json={"text": "hi", "skills": ["weekly-report"], "knowledge_bases": ["k1.ds1", "k2.*"]})
        assert sent.status_code == 202
        bad = await http.post(f"{CONV}/{created['id']}/messages", headers=AUTH,
                              json={"text": "hi", "skills": ["含 空格"]})
        assert bad.status_code == 422
    last = profiles.selections[-1]
    assert last.skills == ("weekly-report",) and last.knowledge_bases == ("k1.ds1", "k2.*")
    assert len(kernel.opened) == 2, "选择变了，本轮开始前以新配置恢复会话"
    await pump.close()


async def test_dropped_conditions_reach_the_profile(sessionmaker):
    """回答上方撤掉的检索条件随消息带上，进本轮运行配置（检索工具从绑定的配置里读）；下一轮不带就恢复。"""
    profiles = Profiles()
    http, pump, _ = client(sessionmaker, profiles)
    async with http:
        created = (await http.post(CONV, json={}, headers=AUTH)).json()
        url = f"{CONV}/{created['id']}/messages"
        sent = await http.post(url, headers=AUTH, json={"text": "再答一次", "dropped_conditions": ["部门 = 财务部"]})
        assert sent.status_code == 202
        assert (await http.post(url, headers=AUTH, json={"text": "下一问"})).status_code == 202
        for bad in ([""], ["x" * 501], [f"条件{i}" for i in range(21)]):
            assert (await http.post(url, headers=AUTH, json={"text": "x", "dropped_conditions": bad})).status_code == 422
    assert [s.dropped_conditions for s in profiles.selections[-2:]] == [("部门 = 财务部",), ()]
    await pump.close()


async def test_mentions_become_the_turns_selection_and_reach_the_host(sessionmaker):
    """@ 的知识库、目录整库查；@ 的文件、标签按库点名；技能进选择。名字交给宿主写进消息；插话时不写。"""
    profiles = Profiles()
    http, pump, kernel = client(sessionmaker, profiles)
    mentions = [
        {"type": "knowledge_base", "id": "builtin.kb1", "name": "制度库"},
        {"type": "folder", "id": "财务/报销", "name": "财务/报销", "bases": ["builtin.kb7", "builtin.kb8"]},
        {"type": "document", "id": "kb2/d1", "name": "差旅办法.pdf", "base": "builtin.kb2", "base_name": "合同库"},
        {"type": "tag", "id": "简报", "name": "简报", "base": "builtin.kb2", "base_name": "合同库"},
        {"type": "skill", "id": "weekly-report", "name": "周报"},
    ]
    async with http:
        created = (await http.post(CONV, json={}, headers=AUTH)).json()
        url = f"{CONV}/{created['id']}/messages"
        assert (await http.post(url, headers=AUTH, json={"text": "报销标准", "mentions": mentions})).status_code == 202
        for bad in ({"type": "document", "id": "kb2/d1", "name": "x"}, {"type": "skill", "id": "周 报", "name": "x"},
                    {"type": "knowledge_base", "id": "kb1", "name": "x"}, {"type": "folder", "id": "a", "name": "a"}):
            assert (await http.post(url, headers=AUTH, json={"text": "x", "mentions": [bad]})).status_code == 422, bad
    chosen = profiles.selections[-1].normalized()
    assert chosen.knowledge_bases == ("builtin.kb1", "builtin.kb2", "builtin.kb7", "builtin.kb8")
    assert [(p.base, p.documents, p.tags) for p in chosen.pins] == [("builtin.kb2", ("kb2/d1",), ("简报",))]
    assert chosen.skills == ("weekly-report",)
    [turn] = kernel.sent
    assert [(m.type, m.id, m.name, m.base, m.base_name) for m in turn.mentions] == [
        ("knowledge_base", "builtin.kb1", "制度库", None, None), ("folder", "财务/报销", "财务/报销", None, None),
        ("document", "kb2/d1", "差旅办法.pdf", "builtin.kb2", "合同库"), ("tag", "简报", "简报", "builtin.kb2", "合同库"),
        ("skill", "weekly-report", "周报", None, None)]
    await pump.close()


async def test_creating_with_the_first_turns_selection_binds_it_up_front(sessionmaker):
    """新会话第一条带 @ 时，建会话就按它定运行配置，第一条发出去不用先释放再重开；选择不合法的照样 422。"""
    profiles = Profiles()
    http, pump, kernel = client(sessionmaker, profiles)
    mentions = [{"type": "document", "id": "kb2/d1", "name": "合同模板.docx", "base": "builtin.kb2", "base_name": "合同库"}]
    async with http:
        created = await http.post(CONV, headers=AUTH, json={"title": "合同模板", "first_turn": {"mentions": mentions}})
        assert created.status_code == 201
        url = f"{CONV}/{created.json()['id']}/messages"
        assert (await http.post(url, headers=AUTH, json={"text": "合同模板有哪些内容", "mentions": mentions})).status_code == 202
        bad = await http.post(CONV, headers=AUTH, json={"first_turn": {"mentions": [{"type": "document", "id": "x"}]}})
        assert bad.status_code == 422
    assert [p.documents for p in profiles.selections[0].normalized().pins] == [("kb2/d1",)]
    assert len(kernel.opened) == 1 and kernel.disposed == []
    await pump.close()


async def test_chosen_language_reaches_the_host(sessionmaker):
    """聊天页选的回答语言随消息带给宿主；不传时为空，由宿主客户端按提问判断。"""
    http, pump, kernel = client(sessionmaker, Profiles())
    async with http:
        created = (await http.post(CONV, json={}, headers=AUTH)).json()
        url = f"{CONV}/{created['id']}/messages"
        assert (await http.post(url, headers=AUTH, json={"text": "Zleap-AI/SAG-Wiki", "language": "zh"})).status_code == 202
        assert (await http.post(url, headers=AUTH, json={"text": "再来"})).status_code == 202
        assert (await http.post(url, headers=AUTH, json={"text": "x", "language": "fr"})).status_code == 422
    assert [turn.language for turn in kernel.sent] == ["zh", None]
    await pump.close()


async def test_answer_mode_reaches_the_host(sessionmaker):
    """回答方式随消息带给宿主，不传是智能体。快速问答只检索知识库：带附件拒收，选的技能与 @ 的技能这一轮不算。"""
    profiles = Profiles()
    http, pump, kernel = client(sessionmaker, profiles, attachments=Files())
    mentions = [{"type": "knowledge_base", "id": "builtin.kb1", "name": "制度库"},
                {"type": "skill", "id": "weekly-report", "name": "周报"}]
    async with http:
        created = (await http.post(CONV, json={}, headers=AUTH)).json()
        url = f"{CONV}/{created['id']}/messages"
        quick = {"text": "报销标准", "answer_mode": "quick", "skills": ["weekly-report"], "mentions": mentions}
        assert (await http.post(url, headers=AUTH, json=quick)).status_code == 202
        assert (await http.post(url, headers=AUTH, json={"text": "整理成表格", "mentions": mentions})).status_code == 202
        with_file = {"text": "x", "answer_mode": "quick", "attachments": ["att_1"]}
        assert (await http.post(url, headers=AUTH, json=with_file)).status_code == 422
        assert (await http.post(url, headers=AUTH, json={"text": "x", "answer_mode": "fast"})).status_code == 422
    assert [turn.answer_mode for turn in kernel.sent] == ["quick", "agent"]
    assert [[m.type for m in turn.mentions] for turn in kernel.sent] == [["knowledge_base"], ["knowledge_base", "skill"]]
    quick_turn, agent_turn = (s.normalized() for s in profiles.selections[-2:])
    assert (quick_turn.skills, quick_turn.knowledge_bases) == (None, ("builtin.kb1",))
    assert agent_turn.skills == ("weekly-report",)
    await pump.close()


class Files:
    async def for_turn(self, conversation, ids):
        if "att_gone" in ids:
            raise AttachmentUnavailable("有附件还没准备好或已失效，请移除后重新上传")
        return tuple(TurnAttachment(name=f"{i}.docx", path=f"/workspace/input/{i}.docx", bytes=1, sha256="b" * 64)
                     for i in ids)


async def test_attachments_ride_along_with_the_message(sessionmaker):
    http, pump, kernel = client(sessionmaker, Profiles(), attachments=Files())
    async with http:
        created = (await http.post(CONV, json={}, headers=AUTH)).json()
        url = f"{CONV}/{created['id']}/messages"
        assert (await http.post(url, headers=AUTH, json={"text": "改一下", "attachments": ["att_1"]})).status_code == 202
        gone = await http.post(url, headers=AUTH, json={"text": "x", "attachments": ["att_gone"]})
        assert (gone.status_code, gone.json()["detail"]) == (409, "有附件还没准备好或已失效，请移除后重新上传")
    assert [[a.path for a in turn.attachments] for turn in kernel.sent] == [["/workspace/input/att_1.docx"]]
    await pump.close()


async def test_profile_unavailable_is_503(sessionmaker):
    profiles = Profiles()
    http, pump, _ = client(sessionmaker, profiles)
    async with http:
        created = (await http.post(CONV, json={}, headers=AUTH)).json()
        profiles.fail = True
        response = await http.post(f"{CONV}/{created['id']}/messages", headers=AUTH, json={"text": "x"})
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "runtime_profile_unavailable"
        refused = await http.post(CONV, json={}, headers=AUTH)
        assert refused.status_code == 503
    await pump.close()
