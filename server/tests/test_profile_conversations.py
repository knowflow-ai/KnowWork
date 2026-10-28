"""每轮重算运行配置：版本变了就以新策略恢复宿主会话；权限收回后下一轮工具不可见、执行被拒。"""

import asyncio

import pytest

import knowwork_server.tools.models  # noqa: F401
from knowwork_server.conversations.listeners import Broadcaster
from knowwork_server.conversations.models import RunEvent
from knowwork_server.conversations.pump import EventPump, PumpTimings
from knowwork_server.conversations.replay import EventFeed
from knowwork_server.conversations.service import ConversationService
from knowwork_server.conversations.tool_credentials import ToolCredentials
from knowwork_server.conversations.writer import BatchWriter
from knowwork_server.kernel.contract import (
    KernelError,
    KnowledgeCatalog,
    KnowledgeEntry,
    SessionNotFound,
    TurnInput,
)
from knowwork_server.profile.clearance import AuthorizedToolRegistry, ToolClearance
from knowwork_server.profile.errors import ProfileUnavailable
from knowwork_server.profile.snapshot import TurnSelection
from knowwork_server.tools import build_tool_service
from knowwork_server.tools.service import ExecuteRequest, ToolAccessError

from .fakes import FakeKernel
from .profile_support import WS, Grants, ensure_profile_tables, registry, skill

pytestmark = pytest.mark.anyio
USER = "ed"


class Skills:
    async def for_session(self, workspace_id, user_id):
        return (skill("weekly-report"),)


class Models:
    async def default_capabilities(self, workspace_id):
        return frozenset({"chat", "vision"})  # 能看图：本文件只关心授权收窄，看图工具的收窄见 test_profile_facts


class Admin:
    async def queryable(self, actor, workspace_id):
        return []


async def editor(user_id, workspace_id):
    return "editor"


@pytest.fixture
async def db(sessionmaker):
    return await ensure_profile_tables(sessionmaker)


class Env:
    def __init__(self, sessionmaker, kernel=None, knowledge_catalog=None):
        from knowwork_server.profile.assembly import build_profiles

        self.kernel = kernel or FakeKernel()
        self.grants = Grants({USER: "editor"})
        clearance = ToolClearance(self.grants, builtin=frozenset({"kw_now"}))
        self.registry = AuthorizedToolRegistry(registry(), clearance)
        self.tools = build_tool_service("m" * 40, sessionmaker, registry=self.registry)
        self.profiles = build_profiles(sessionmaker, self.registry, clearance, skills=Skills(), models=Models(),
                                       knowledge=Admin(), role_of=editor)

        async def issue(conversation):
            return await self.tools.issue_session_token(conversation.workspace_id, conversation.id,
                                                        user_id=conversation.user_id)

        writer, broadcaster = BatchWriter(sessionmaker), Broadcaster()
        self.pump = EventPump(self.kernel, sessionmaker, writer, broadcaster, timings=PumpTimings(idle_timeout=0.2))
        self.service = ConversationService(self.kernel, sessionmaker, self.pump,
                                           EventFeed(sessionmaker, writer, broadcaster, self.pump.produced_upto),
                                           tool_credentials=ToolCredentials(self.kernel, issue),
                                           profiles=self.profiles, knowledge_catalog=knowledge_catalog,
                                           resume_retry_delays=(0.01,))

    def deny(self, index=-1):
        return set(self.kernel.opened[index].policy.deny)

    def claims(self, index=-1):
        return self.tools.verify(self.kernel.opened[index].tools_token)


async def test_create_opens_with_compiled_deny_and_records_version(db):
    env = Env(db)
    conversation = await env.service.create(workspace_id=WS, user_id=USER)
    assert env.deny() == {"crm_write", "erp_read"}
    assert await env.profiles.bound_version(conversation) == (await env.profiles.compile(conversation)).version
    await env.pump.close()


async def test_unchanged_profile_does_not_reopen(db):
    env = Env(db)
    conversation = await env.service.create(workspace_id=WS, user_id=USER)
    await env.service.send(conversation, TurnInput(text="一"))
    await env.service.send(conversation, TurnInput(text="二"))
    assert len(env.kernel.opened) == 1 and env.kernel.disposed == []
    await env.pump.close()


async def test_grant_then_revoke_reopens_each_time(db):
    env = Env(db)
    conversation = await env.service.create(workspace_id=WS, user_id=USER)
    env.grants.grant(USER, "tool:crm_write")
    await env.service.send(conversation, TurnInput(text="写台账"))
    assert env.kernel.disposed == [conversation.id]
    assert env.kernel.opened[-1].resume is True
    assert "crm_write" not in env.deny() and "crm_write" in env.claims().tools
    granted_claims = env.claims()

    env.grants.revoke(USER, "tool:crm_write")
    await env.service.send(conversation, TurnInput(text="再写一次"))
    assert len(env.kernel.disposed) == 2 and len(env.kernel.opened) == 3
    assert "crm_write" in env.deny(), "权限收回后下一轮宿主应收窄该工具"
    assert "crm_write" not in env.claims().tools
    with pytest.raises(ToolAccessError) as denied:
        await env.tools.execute(granted_claims, ExecuteRequest(tool_name="crm_write", arguments={"q": "x"},
                                                               agent_session_id=conversation.id,
                                                               call_key=f"{conversation.id}:call-1"))
    assert denied.value.code == "tool_not_available", "旧令牌在执行时也要按当前授权被拒"
    await env.pump.close()


async def test_selection_change_reopens(db):
    env = Env(db)
    conversation = await env.service.create(workspace_id=WS, user_id=USER)
    await env.service.send(conversation, TurnInput(text="用周报技能"), TurnSelection(skills=("weekly-report",)))
    assert len(env.kernel.opened) == 2
    await env.service.send(conversation, TurnInput(text="还是周报"), TurnSelection(skills=("weekly-report",)))
    assert len(env.kernel.opened) == 2
    await env.pump.close()


async def test_lost_host_session_resumes_with_current_profile(db):
    env = Env(db)
    conversation = await env.service.create(workspace_id=WS, user_id=USER)
    env.kernel.send_effects = [SessionNotFound("session_not_found", "x", 404)]
    await env.service.send(conversation, TurnInput(text="宿主重启后"))
    assert [s.resume for s in env.kernel.opened] == [False, True]
    assert env.deny() == {"crm_write", "erp_read"}
    await env.pump.close()


async def test_reopen_failure_does_not_record_new_version(db):
    env = Env(db)
    conversation = await env.service.create(workspace_id=WS, user_id=USER)
    old = await env.profiles.bound_version(conversation)
    env.grants.grant(USER, "tool:erp_read")
    env.kernel.open_effects = [OSError("host down")]
    with pytest.raises(OSError):
        await env.service.send(conversation, TurnInput(text="x"))
    assert await env.profiles.bound_version(conversation) == old
    await env.service.send(conversation, TurnInput(text="再试"))
    assert "erp_read" not in env.deny()
    assert await env.profiles.bound_version(conversation) != old
    await env.pump.close()


# DSH 第一轮跑起来才写会话记录：一轮都没开始过的会话「接着记录」打开时宿主报 session_open_failed
NOTHING_PERSISTED = KernelError("session_open_failed", "会话打开失败，详情见宿主日志", 400)
SKILL = TurnSelection(skills=("weekly-report",))


async def test_first_message_that_changes_the_profile_opens_fresh_when_nothing_was_persisted(db):
    """新会话第一条消息就 @ 了文件（或选了技能），配置版本和建会话时不同，要重开；宿主那边还没有记录，
    「接着记录」打不开，改成新开（2026-10-26 新会话第一条带 @ 必然报「运行时暂时不可用」）。"""
    env = Env(db)
    conversation = await env.service.create(workspace_id=WS, user_id=USER)
    env.kernel.open_effects = [NOTHING_PERSISTED]
    await env.service.send(conversation, TurnInput(text="用周报技能"), SKILL)
    assert [s.resume for s in env.kernel.opened] == [False, False]
    assert env.kernel.sent[-1].text == "用周报技能"
    assert await env.profiles.bound_version(conversation) == (await env.profiles.compile(conversation, SKILL)).version
    await env.pump.close()


async def test_a_conversation_with_turns_is_never_silently_reopened_fresh(db):
    """跑过回合的会话打不开时照旧报错：新开会丢掉前面的上下文，不能悄悄这么做。"""
    env = Env(db)
    conversation = await env.service.create(workspace_id=WS, user_id=USER)
    async with db() as session:
        session.add(RunEvent(conversation_id=conversation.id, seq=1, workspace_id=WS, host_epoch="e1", host_cursor=1,
                             kind="session/event", data={"type": "turn/start", "data": {"turn": 1}}))
        await session.commit()
    env.kernel.open_effects = [NOTHING_PERSISTED]
    with pytest.raises(KernelError):
        await env.service.send(conversation, TurnInput(text="用周报技能"), SKILL)
    assert [s.resume for s in env.kernel.opened] == [False]
    await env.pump.close()


async def test_host_restart_before_the_first_message_opens_fresh(db):
    env = Env(db)
    conversation = await env.service.create(workspace_id=WS, user_id=USER)
    env.kernel.send_effects = [SessionNotFound("session_not_found", "x", 404)]
    env.kernel.open_effects = [NOTHING_PERSISTED]
    await env.service.send(conversation, TurnInput(text="宿主刚重启"))
    assert [s.resume for s in env.kernel.opened] == [False, False]
    assert env.kernel.sent[-1].text == "宿主刚重启"
    await env.pump.close()


async def test_creating_with_the_first_selection_does_not_reopen(db):
    """建会话时就带上第一条消息的选择：第一条发出去不用先释放再重开。"""
    env = Env(db)
    conversation = await env.service.create(workspace_id=WS, user_id=USER, selection=SKILL)
    await env.service.send(conversation, TurnInput(text="用周报技能"), SKILL)
    assert len(env.kernel.opened) == 1 and env.kernel.disposed == []
    await env.pump.close()


async def test_profile_unavailable_blocks_create_without_orphan(db):
    env = Env(db)

    async def broken(conversation, selection=None):
        raise ProfileUnavailable()

    env.profiles.compile = broken
    with pytest.raises(ProfileUnavailable):
        await env.service.create(workspace_id=WS, user_id="orphan-check")
    assert env.kernel.opened == []
    page = await env.service.list_page(workspace_id=WS, user_id="orphan-check")
    assert page.items == ()
    await env.pump.close()


async def test_steer_does_not_reopen_mid_turn(db):
    env = Env(db)
    conversation = await env.service.create(workspace_id=WS, user_id=USER)
    env.grants.grant(USER, "tool:erp_read")
    await env.service.send(conversation, TurnInput(text="插一句", mode="steer"))
    assert env.kernel.disposed == [] and len(env.kernel.opened) == 1
    await env.service.send(conversation, TurnInput(text="下一轮"))
    assert "erp_read" not in env.deny()
    await env.pump.close()


CATALOG = KnowledgeCatalog(bases=(KnowledgeEntry(id="builtin.kb1", name="合同库"),))


async def test_each_turn_carries_the_knowledge_catalog(db):
    seen = []

    async def catalog(conversation, profile):
        seen.append(profile.version)
        return CATALOG

    env = Env(db, knowledge_catalog=catalog)
    conversation = await env.service.create(workspace_id=WS, user_id=USER)
    await env.service.send(conversation, TurnInput(text="查合同"))
    assert env.kernel.sent[-1].knowledge == CATALOG and len(seen) == 1
    await env.pump.close()


async def test_a_failing_or_slow_catalog_does_not_block_the_turn(db, monkeypatch):
    async def broken(conversation, profile):
        raise RuntimeError("知识源挂了")

    env = Env(db, knowledge_catalog=broken)
    conversation = await env.service.create(workspace_id=WS, user_id=USER)
    await env.service.send(conversation, TurnInput(text="查合同"))
    assert env.kernel.sent[-1].knowledge is None, "没算出来就不带，宿主沿用上一轮的"
    await env.pump.close()

    async def slow(conversation, profile):
        await asyncio.sleep(5)
        return CATALOG

    monkeypatch.setattr("knowwork_server.conversations.service.CATALOG_TIMEOUT", 0.05)
    env = Env(db, knowledge_catalog=slow)
    conversation = await env.service.create(workspace_id=WS, user_id=USER)
    await env.service.send(conversation, TurnInput(text="查合同"))
    assert env.kernel.sent[-1].knowledge is None
    await env.pump.close()
