"""会话与配置版本的绑定：同版本快照只存一份、换绑更新、本轮知识范围、随会话删除。真实 PostgreSQL。"""

import pytest
from sqlalchemy import delete, func, select

from knowwork_server.conversations.models import Conversation
from knowwork_server.profile.compiler import KnowledgeEntry, ProfileFacts, compile_profile
from knowwork_server.profile.snapshot import NO_SELECTION, PinnedScope, TurnSelection
from knowwork_server.tools.registry import ToolContext, ToolFailure

from .profile_support import ensure_profile_tables

pytestmark = pytest.mark.anyio


@pytest.fixture
async def db(sessionmaker):
    return await ensure_profile_tables(sessionmaker)


def bindings_for(sessionmaker):
    from knowwork_server.profile.bindings import ProfileBindings

    return ProfileBindings(sessionmaker)


def tables():
    from knowwork_server.profile.models import KernelBinding, RuntimeProfileRow

    return KernelBinding, RuntimeProfileRow


def profile(role="editor", selection=NO_SELECTION):
    return compile_profile(ProfileFacts(workspace_id="default", user_id="dev-user", role=role, member=True,
                                        knowledge=(KnowledgeEntry("k1", "制度库", "knowflow"),), selection=selection))


async def count(sessionmaker, model):
    async with sessionmaker() as session:
        return await session.scalar(select(func.count()).select_from(model))


async def test_bind_and_rebind(db, conversation_id):
    bindings = bindings_for(db)
    assert await bindings.version_of("default", conversation_id) is None
    first, second = profile(), profile(role="admin")
    await bindings.bind("default", conversation_id, first)
    assert await bindings.version_of("default", conversation_id) == first.version
    assert await bindings.current("default", conversation_id) == first
    await bindings.bind("default", conversation_id, second)
    assert await bindings.version_of("default", conversation_id) == second.version
    assert await bindings.current("default", conversation_id) == second


async def test_same_snapshot_is_stored_once(db, conversation_id):
    bindings = bindings_for(db)
    before = await count(db, tables()[1])
    shared = profile(role="owner")  # 和别的测试存过的快照都不一样
    await bindings.bind("default", conversation_id, shared)
    await bindings.bind("default", conversation_id, shared)
    assert await count(db, tables()[1]) == before + 1


async def test_tools_read_the_turns_selection(db, conversation_id):
    """知识工具一次读出本轮绑定的配置：选了的知识库、撤掉的条件、@ 点名的文件与标签。"""
    bindings = bindings_for(db)
    ctx = ToolContext(workspace_id="default", session_id=conversation_id, user_id="dev-user")
    assert await bindings.for_tool(ctx) is None, "还没绑定配置时不限定"
    chosen = TurnSelection(knowledge_bases=("k1.ds1",), dropped_conditions=("部门 = 财务部", "部门 = 财务部"),
                           pins=(PinnedScope(base="k1.ds2", documents=("ds2/d1",)),))
    await bindings.bind("default", conversation_id, profile(selection=chosen))
    bound = await bindings.for_tool(ctx)
    assert bound.bound_knowledge == ("k1.ds1", "k1.ds2"), "点名的库并进本轮可查的库"
    assert bound.dropped_conditions == ("部门 = 财务部",)
    assert bound.pins == (PinnedScope(base="k1.ds2", documents=("ds2/d1",)),)


async def test_tools_fail_closed_when_the_binding_cannot_be_read(db):
    class Broken(type(bindings_for(db))):
        async def current(self, workspace_id, conversation_id):
            raise RuntimeError("db down")

    with pytest.raises(ToolFailure):
        await Broken(db).for_tool(ToolContext(workspace_id="default", session_id="c", user_id="u"))


async def test_binding_goes_with_conversation(db, conversation_id):
    bindings = bindings_for(db)
    await bindings.bind("default", conversation_id, profile())
    await bindings.erase("default", conversation_id)
    assert await bindings.version_of("default", conversation_id) is None
    await bindings.bind("default", conversation_id, profile())
    binding = tables()[0]
    async with db() as session:
        await session.execute(delete(Conversation).where(Conversation.id == conversation_id))
        await session.commit()
        left = await session.scalar(select(func.count()).select_from(binding)
                                    .where(binding.conversation_id == conversation_id))
    assert left == 0
