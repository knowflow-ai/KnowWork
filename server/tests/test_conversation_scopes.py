"""扩展给对话的范围（conversations/scopes.py）：没登记时和没有这个功能一样；第一个管这段对话的提供者说了算；
固定的回答方式每一轮都按它（输入框里选的不算）；前端取得到对话顶部的名字和固定的方式；编译运行配置时按这段对话的范围
收窄；提供者出错时这一轮不发、取范围的接口 503、会话不打开（失败关闭）。"""

import dataclasses

import httpx
import pytest
from fastapi import FastAPI

from knowwork_server.conversations.api import build_router
from knowwork_server.conversations.listeners import Broadcaster
from knowwork_server.conversations.pump import EventPump
from knowwork_server.conversations.replay import EventFeed
from knowwork_server.conversations.scopes import ConversationScope, ConversationScopes, ScopeUnavailable
from knowwork_server.conversations.service import ConversationService
from knowwork_server.conversations.writer import BatchWriter
from knowwork_server.profile.errors import ProfileUnavailable

from .fakes import FakeKernel, token_actor
from .profile_support import WS
from .test_profile_api import Profiles
from .test_profile_facts import compiler

pytestmark = pytest.mark.anyio
CONV = "/api/workspaces/default/conversations"
TOKEN = "e" * 48
AUTH = {"authorization": f"Bearer {TOKEN}"}


class Provider:
    """管 bound 里的那几段对话；fail 时出错。"""

    def __init__(self, scope: ConversationScope):
        self.scope, self.bound, self.fail = scope, set(), False

    async def __call__(self, workspace_id, conversation_id):
        if self.fail:
            raise RuntimeError("专家表连不上")
        return self.scope if conversation_id in self.bound else None


def client(sessionmaker, scopes):
    kernel = FakeKernel()
    writer, broadcaster = BatchWriter(sessionmaker), Broadcaster()
    pump = EventPump(kernel, sessionmaker, writer, broadcaster)
    service = ConversationService(kernel, sessionmaker, pump, EventFeed(sessionmaker, writer, broadcaster,
                                                                        pump.produced_upto), profiles=Profiles(),
                                  scopes=scopes)
    app = FastAPI()
    app.include_router(build_router(service, token_actor(TOKEN), scopes=scopes))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://public"), pump, kernel


async def test_registry_without_providers_and_first_one_wins():
    scopes = ConversationScopes()
    assert not scopes and await scopes.of("ws", "c1") is None
    first, second = Provider(ConversationScope("经营分析", answer_mode="quick")), Provider(ConversationScope("别的"))
    first.bound, second.bound = {"c1"}, {"c1", "c2"}
    scopes.add(first)
    scopes.add(second)
    assert (await scopes.of("ws", "c1")).label == "经营分析"
    assert (await scopes.of("ws", "c2")).label == "别的"
    assert await scopes.narrowing("ws", "c3") is None
    first.fail = True
    with pytest.raises(ScopeUnavailable):
        await scopes.of("ws", "c2")
    with pytest.raises(ValueError):
        ConversationScope("")
    with pytest.raises(ValueError):
        ConversationScope("x", answer_mode="Not-A-Mode")


async def test_fixed_answer_mode_wins_and_the_ui_can_read_the_scope(sessionmaker):
    scopes = ConversationScopes()
    provider = Provider(ConversationScope("经营分析", answer_mode="quick"))
    scopes.add(provider)
    http, pump, kernel = client(sessionmaker, scopes)
    async with http:
        bound = (await http.post(CONV, json={}, headers=AUTH)).json()["id"]
        free = (await http.post(CONV, json={}, headers=AUTH)).json()["id"]
        provider.bound = {bound}
        for conversation in (bound, free):
            sent = await http.post(f"{CONV}/{conversation}/messages", headers=AUTH, json={"text": "上月营收", "answer_mode": "agent"})
            assert sent.status_code == 202
        assert (await http.get(f"{CONV}/{bound}/scope", headers=AUTH)).json() == {"label": "经营分析", "answerMode": "quick"}
        assert (await http.get(f"{CONV}/{free}/scope", headers=AUTH)).json() is None
        provider.fail = True
        failed = await http.post(f"{CONV}/{bound}/messages", headers=AUTH, json={"text": "再算一次"})
        assert failed.status_code == 503 and "设置暂时取不到" in failed.json()["detail"]
        assert (await http.get(f"{CONV}/{bound}/scope", headers=AUTH)).status_code == 503
    assert [turn.answer_mode for turn in kernel.sent] == ["quick", "agent"], "范围里固定的方式每一轮都按它"
    await pump.close()


async def test_profiles_are_narrowed_to_the_conversation_scope():
    scopes = ConversationScopes()
    provider = Provider(ConversationScope("经营分析", knowledge_bases=("k1.other",), skills=()))
    provider.bound = {"c_bound"}
    scopes.add(provider)
    comp, _ = compiler()
    comp = type(comp)(dataclasses.replace(comp._s, narrowing_of=scopes.narrowing))
    free = await comp.compile(workspace_id=WS, user_id="ed", session_id="c_free")
    bound = await comp.compile(workspace_id=WS, user_id="ed", session_id="c_bound")
    assert [s.name for s in free.skills] == ["a"] and [k.id for k in free.knowledge] == ["k1"]
    assert bound.skills == () and bound.knowledge == () and bound.bound_knowledge == ("k1.other",)
    provider.fail = True
    with pytest.raises(ProfileUnavailable):
        await comp.compile(workspace_id=WS, user_id="ed", session_id="c_bound")
