"""个人访问令牌的 MCP 服务：登记人用自己的令牌测试、同步；成员在「设置 → 连接」里连自己的，调用带本人凭据；
没连接的不进对话、模型知道去哪儿让用户连接；对方拒绝了某人的凭据就标成失效，不回落到共用凭据。"""

import pytest
from sqlalchemy import select, text

from knowwork_server.profile.clearance import ToolClearance
from knowwork_server.tools.mcp import build_mcp
from knowwork_server.tools.mcp.errors import InvalidInput, NotFound, Unreachable
from knowwork_server.tools.mcp.models import McpConnectionRow
from knowwork_server.tools.mcp.schemas import McpServerCreate, McpServerPatch, McpToolPatch, TokenConfig
from knowwork_server.tools.registry import ToolContext, ToolFailure

from .conftest import MASTER, PUBLIC

pytestmark = pytest.mark.anyio

GOOD = "mcp-test-token-1234"  # 桩服务只认 Bearer 加这个令牌
ADD = "mcp__demo__add"
CONFIG = TokenConfig(help="在个人设置 → 访问令牌里生成")


def ctx(user: str) -> ToolContext:
    return ToolContext(workspace_id="ws1", session_id="s1", user_id=user)


async def register(env, stub):
    body = McpServerCreate(name="demo", title="演示服务", url=stub.url, auth="user_token", token_config=CONFIG,
                           token=GOOD)
    return await env.mcp.admin.create("alice", "ws1", body)


async def test_register_needs_the_registrants_own_token_and_keeps_it_as_their_connection(env, stub):
    with pytest.raises(InvalidInput):
        await env.mcp.admin.create("alice", "ws1", McpServerCreate(name="demo", title="演示服务", url=stub.url,
                                                                   auth="user_token"))
    with pytest.raises(Unreachable, match="HTTP 401"):
        await env.mcp.admin.create("alice", "ws1", McpServerCreate(name="demo", title="演示服务", url=stub.url,
                                                                   auth="user_token", token="wrong"))
    record = await register(env, stub)
    assert (record.auth, record.headers_ciphertext, len(record.tools)) == ("user_token", None, 4)
    assert record.auth_config == {"header": "Authorization", "prefix": "Bearer ", "help": "在个人设置 → 访问令牌里生成"}
    mine = await env.mcp.connections.for_user("ws1", "alice")
    assert mine[record.id].usable and mine[record.id].display.endswith("1234")
    assert {s.name for s in await env.mcp.registry.available(ctx("alice"))} >= {ADD}


async def test_members_connect_their_own_and_unconnected_services_stay_out_of_the_conversation(env, stub):
    record = await register(env, stub)
    me, registry = env.mcp.me, env.mcp.registry
    assert await registry.available(ctx("carol")) == [], "没连接的服务不进对话"
    [row] = await me.list("carol", "ws1")
    assert (row.server_id, row.status, row.help) == (record.id, "none", "在个人设置 → 访问令牌里生成")
    assert "演示服务" in await me.note(ctx("carol")) and "设置 → 连接" in await me.note(ctx("carol"))
    with pytest.raises(InvalidInput, match="拒绝了这份令牌"):
        await me.connect("carol", "ws1", record.id, "wrong")
    connected = await me.connect("carol", "ws1", record.id, GOOD)
    assert connected.status == "connected" and GOOD not in connected.display
    assert await me.note(ctx("carol")) is None
    assert ADD in {s.name for s in await registry.available(ctx("carol"))}
    assert await registry.invoke(ctx("carol"), ADD, {"a": 1, "b": 2}) == "3"
    await me.disconnect("carol", "ws1", record.id)
    with pytest.raises(NotFound):
        await me.disconnect("carol", "ws1", record.id)


async def test_rejected_credentials_are_marked_invalid_and_never_fall_back(env, stub):
    record = await register(env, stub)
    await env.mcp.connections.save("ws1", record.id, "carol", "revoked-later")
    registry = env.mcp.registry
    with pytest.raises(ToolFailure, match="连接失效了"):
        await registry.invoke(ctx("carol"), ADD, {"a": 1, "b": 2})
    [row] = await env.mcp.me.list("carol", "ws1")
    assert row.status == "invalid"
    assert ADD in {s.name for s in await registry.available(ctx("carol"))}, "失效的照样给工具，调用时提示重连"
    with pytest.raises(ToolFailure, match="还没连接或连接已失效"):
        await registry.invoke(ctx("carol"), ADD, {"a": 1, "b": 2})
    assert await registry.invoke(ctx("alice"), ADD, {"a": 1, "b": 2}) == "3", "别人的连接不受影响"


async def test_sync_and_update_use_the_admins_own_connection(env, stub):
    record = await register(env, stub)
    with pytest.raises(Unreachable, match="先在「设置 → 连接」里连接"):
        await env.mcp.admin.sync("bob", "ws1", record.id)
    await env.mcp.me.connect("bob", "ws1", record.id, GOOD)
    assert len((await env.mcp.admin.sync("bob", "ws1", record.id)).tools) == 4
    renamed = await env.mcp.admin.update("bob", "ws1", record.id, McpServerPatch(title="新名字"))
    assert renamed.title == "新名字"
    shared = await env.mcp.admin.create("alice", "ws1", McpServerCreate(
        name="shared", title="共用", url=stub.url, headers={"Authorization": f"Bearer {GOOD}"}))
    with pytest.raises(InvalidInput, match="要填你自己的令牌"):
        await env.mcp.admin.update("alice", "ws1", shared.id, McpServerPatch(auth="user_token"))
    switched = await env.mcp.admin.update("alice", "ws1", shared.id, McpServerPatch(auth="user_token", token=GOOD))
    assert (switched.auth, switched.headers_ciphertext) == ("user_token", None), "改成个人访问令牌后不留共用凭据"


async def test_only_services_the_member_may_use_are_listed(env, stub):
    record = await register(env, stub)
    narrowed = build_mcp(MASTER, env.sessionmaker, env.access, public_base_url=PUBLIC,
                         permits=ToolClearance(env.access, builtin=frozenset()).permits)
    assert [row.server_id for row in await narrowed.me.list("carol", "ws1")] == [record.id], "新服务默认全体成员"
    await env.access.set_users("alice", "ws1", "tool:mcp__demo__*", [])
    assert await narrowed.me.list("carol", "ws1") == []
    with pytest.raises(NotFound):
        await narrowed.me.connect("carol", "ws1", record.id, GOOD)
    assert await narrowed.me.note(ctx("carol")) is None


async def test_member_removed_loses_their_connections(env, stub):
    record = await register(env, stub)
    await env.mcp.me.connect("dave", "ws1", record.id, GOOD)
    env.access.on_member_removed(env.mcp.connections.forget_member)
    await env.access.remove_member("alice", "ws1", "dave")
    async with env.sessionmaker() as session, session.begin():
        await session.execute(text("SELECT set_config('app.workspace_id', 'ws1', true)"))
        users = (await session.scalars(select(McpConnectionRow.user_id))).all()
    assert users == ["alice"]


async def test_deleting_the_service_removes_everyones_connections(env, stub):
    record = await register(env, stub)
    await env.mcp.me.connect("carol", "ws1", record.id, GOOD)
    await env.mcp.admin.delete("alice", "ws1", record.id)
    assert await env.mcp.connections.for_user("ws1", "carol") == {}
    assert await env.mcp.me.list("carol", "ws1") == []


async def test_services_that_need_no_connection_are_listed_as_ready_with_their_tools(env, stub):
    """成员要知道能让 AI 用哪些服务：不用连接的也列出来（可以直接用），带上本人能用的工具，禁用的不列。"""
    shared = await env.mcp.admin.create("alice", "ws1", McpServerCreate(
        name="shared", title="共用", url=stub.url, headers={"Authorization": f"Bearer {GOOD}"}))
    await env.mcp.admin.configure_tool("alice", "ws1", shared.id, "mcp__shared__delete_item",
                                       McpToolPatch(policy="block"))
    [row] = await env.mcp.me.list("carol", "ws1")
    assert (row.server_id, row.status, row.auth) == (shared.id, "ready", "shared")
    tools = {tool.name: tool.description for tool in row.tools}
    assert tools["add"] == "两数相加" and "delete_item" not in tools, "禁用的工具不列"
    with pytest.raises(NotFound):
        await env.mcp.me.connect("carol", "ws1", shared.id, GOOD)


async def test_mention_menu_lists_services_whose_tools_are_in_the_conversation(env, stub):
    """@ 菜单里的 MCP 服务：不用连接的、已连接的列出来；还没连接的不列（工具不在对话里，@ 了也用不上）。"""
    per_user = await register(env, stub)
    await env.mcp.admin.create("alice", "ws1", McpServerCreate(
        name="shared", title="共用", url=stub.url, headers={"Authorization": f"Bearer {GOOD}"}))
    listed = await env.mcp.me.mentionable("carol", "ws1")
    assert listed == [{"name": "shared", "title": "共用", "tools": 4, "status": "ready"}]
    await env.mcp.me.connect("carol", "ws1", per_user.id, GOOD)
    assert [(s["name"], s["status"]) for s in await env.mcp.me.mentionable("carol", "ws1")] == [
        ("demo", "connected"), ("shared", "ready")]
