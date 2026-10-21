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
        created = (await http.post("/api/conversations", json={}, headers=AUTH)).json()
        sent = await http.post(f"/api/conversations/{created['id']}/messages", headers=AUTH,
                               json={"text": "hi", "skills": ["weekly-report"], "knowledge_bases": ["k1.ds1", "k2.*"]})
        assert sent.status_code == 202
        bad = await http.post(f"/api/conversations/{created['id']}/messages", headers=AUTH,
                              json={"text": "hi", "skills": ["含 空格"]})
        assert bad.status_code == 422
    last = profiles.selections[-1]
    assert last.skills == ("weekly-report",) and last.knowledge_bases == ("k1.ds1", "k2.*")
    assert len(kernel.opened) == 2, "选择变了，本轮开始前以新配置恢复会话"
    await pump.close()


async def test_chosen_language_reaches_the_host(sessionmaker):
    """聊天页选的回答语言随消息带给宿主；不传时为空，由宿主客户端按提问判断。"""
    http, pump, kernel = client(sessionmaker, Profiles())
    async with http:
        created = (await http.post("/api/conversations", json={}, headers=AUTH)).json()
        url = f"/api/conversations/{created['id']}/messages"
        assert (await http.post(url, headers=AUTH, json={"text": "Zleap-AI/SAG-Wiki", "language": "zh"})).status_code == 202
        assert (await http.post(url, headers=AUTH, json={"text": "再来"})).status_code == 202
        assert (await http.post(url, headers=AUTH, json={"text": "x", "language": "fr"})).status_code == 422
    assert [turn.language for turn in kernel.sent] == ["zh", None]
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
        created = (await http.post("/api/conversations", json={}, headers=AUTH)).json()
        url = f"/api/conversations/{created['id']}/messages"
        assert (await http.post(url, headers=AUTH, json={"text": "改一下", "attachments": ["att_1"]})).status_code == 202
        gone = await http.post(url, headers=AUTH, json={"text": "x", "attachments": ["att_gone"]})
        assert (gone.status_code, gone.json()["detail"]) == (409, "有附件还没准备好或已失效，请移除后重新上传")
    assert [[a.path for a in turn.attachments] for turn in kernel.sent] == [["/workspace/input/att_1.docx"]]
    await pump.close()


async def test_profile_unavailable_is_503(sessionmaker):
    profiles = Profiles()
    http, pump, _ = client(sessionmaker, profiles)
    async with http:
        created = (await http.post("/api/conversations", json={}, headers=AUTH)).json()
        profiles.fail = True
        response = await http.post(f"/api/conversations/{created['id']}/messages", headers=AUTH, json={"text": "x"})
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "runtime_profile_unavailable"
        refused = await http.post("/api/conversations", json={}, headers=AUTH)
        assert refused.status_code == 503
    await pump.close()
