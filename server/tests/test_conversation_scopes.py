"""扩展给对话的范围（conversations/scopes.py）：没登记时和没有这个功能一样；第一个管这段对话的提供者说了算；
固定的回答方式每一轮都按它（输入框里选的不算）；前端取得到对话顶部的名字和固定的方式；编译运行配置时按这段对话的范围
收窄；提供者出错时这一轮不发、取范围的接口 503、会话不打开（失败关闭）。"""

import dataclasses

import httpx
import pytest
from fastapi import FastAPI

from knowwork_server.conversations.api import build_router
from knowwork_server.conversations.device import DeviceConversations
from knowwork_server.conversations.listeners import Broadcaster
from knowwork_server.conversations.models import Conversation
from knowwork_server.conversations.pump import EventPump
from knowwork_server.conversations.replay import EventFeed
from knowwork_server.conversations.scopes import (
    ConversationScope,
    ConversationScopes,
    ScopeClaim,
    ScopeRejected,
    ScopeUnavailable,
)
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


class Claimer:
    """扩展登记的认领：认得的 id 挂上（provider 从此管这段对话），别的拒绝；记下挂上时对话已经在不在、撤掉了哪些。"""

    def __init__(self, provider: Provider, sessionmaker):
        self.provider, self.sessionmaker, self.claimed, self.released = provider, sessionmaker, [], []

    async def claim(self, workspace_id, conversation_id, user_id, item_id):
        if item_id != "ex_1":
            raise ScopeRejected("没有这个专家")
        async with self.sessionmaker() as session:
            exists = await session.get(Conversation, conversation_id) is not None
        self.claimed.append((workspace_id, conversation_id, user_id, item_id, exists))
        self.provider.bound.add(conversation_id)

    async def release(self, workspace_id, conversation_id):
        self.released.append(conversation_id)
        self.provider.bound.discard(conversation_id)


class Nothing:
    async def claim(self, *args):
        return None

    async def release(self, *args):
        return None


async def test_claim_registry_rejects_unknown_kinds():
    scopes = ConversationScopes()
    with pytest.raises(ScopeRejected):
        await scopes.claim("ws", "c1", "u1", ScopeClaim("expert", "ex_1"))

    scopes.add_claimer("expert", Nothing())
    with pytest.raises(ValueError):
        scopes.add_claimer("expert", Nothing())
    with pytest.raises(ValueError):
        scopes.add_claimer("Bad-Kind", Nothing())


async def test_a_conversation_is_created_with_its_scope_claimed_before_the_profile_compiles(sessionmaker):
    """建对话时带上要挂的东西：对话先建出来再挂，挂上了才编译运行配置、打开宿主会话（第一轮就按专家的范围）；
    挂不上不建、对话收回。"""
    scopes = ConversationScopes()
    provider = Provider(ConversationScope("经营分析", answer_mode="quick"))
    scopes.add(provider)
    claimer = Claimer(provider, sessionmaker)
    scopes.add_claimer("expert", claimer)
    http, pump, kernel = client(sessionmaker, scopes)
    async with http:
        made = await http.post(CONV, json={"title": "问经营", "scope": {"kind": "expert", "id": "ex_1"}}, headers=AUTH)
        assert made.status_code == 201
        cid = made.json()["id"]
        assert claimer.claimed == [(WS, cid, "dev-user", "ex_1", True)], "对话建出来再挂，挂的是建它的人"
        assert (await http.get(f"{CONV}/{cid}/scope", headers=AUTH)).json() == {"label": "经营分析", "answerMode": "quick"}
        before = {c["id"] for c in (await http.get(CONV, headers=AUTH)).json()["items"]}
        refused = await http.post(CONV, json={"scope": {"kind": "expert", "id": "ex_nope"}}, headers=AUTH)
        assert refused.status_code == 404 and refused.json()["detail"] == "没有这个专家"
        unknown = await http.post(CONV, json={"scope": {"kind": "persona", "id": "x"}}, headers=AUTH)
        assert unknown.status_code == 404
        after = {c["id"] for c in (await http.get(CONV, headers=AUTH)).json()["items"]}
        assert cid in before and after == before, "挂不上的不留对话"
        assert (await http.delete(f"{CONV}/{cid}", headers=AUTH)).status_code == 204
        assert cid in claimer.released and await scopes.of(WS, cid) is None, "删了对话，挂着的一起撤掉"
    assert len(kernel.opened) == 1, "挂不上的不开宿主会话"
    await pump.close()


async def test_a_device_conversation_claims_its_scope_the_same_way(sessionmaker):
    scopes = ConversationScopes()
    provider = Provider(ConversationScope("经营分析"))
    scopes.add(provider)
    claimer = Claimer(provider, sessionmaker)
    scopes.add_claimer("expert", claimer)
    kernel = FakeKernel()
    writer, broadcaster = BatchWriter(sessionmaker), Broadcaster()
    pump = EventPump(kernel, sessionmaker, writer, broadcaster)
    service = ConversationService(kernel, sessionmaker, pump, EventFeed(sessionmaker, writer, broadcaster,
                                                                        pump.produced_upto), scopes=scopes)
    device = DeviceConversations(service, pump)
    made = await device.create(workspace_id=WS, user_id="u1", scope=ScopeClaim("expert", "ex_1"))
    assert claimer.claimed == [(WS, made.id, "u1", "ex_1", True)]
    assert (await scopes.of(WS, made.id)).label == "经营分析"
    async def count() -> int:
        async with sessionmaker() as session:
            return len((await session.execute(Conversation.__table__.select())).all())

    before = await count()
    with pytest.raises(ScopeRejected):
        await device.create(workspace_id=WS, user_id="u1", scope=ScopeClaim("expert", "ex_nope"))
    assert await count() == before, "挂不上的对话收回"
    assert len(claimer.released) == 1, "没挂上的也撤一次，不留半截"
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
