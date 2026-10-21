"""登记、同步、工具设置与注册表：真实数据库（行级安全）加真实 MCP 服务。"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from knowwork_server.profile.clearance import AuthorizedToolRegistry, ToolClearance
from knowwork_server.tools.mcp.errors import Conflict, Forbidden, Unreachable
from knowwork_server.tools.mcp.schemas import McpServerCreate, McpServerPatch, McpToolPatch
from knowwork_server.tools.registry import ToolContext, ToolFailure

from .conftest import AUTH, TOKEN

pytestmark = pytest.mark.anyio
ADD, TICKET, DELETE = "mcp__demo__add", "mcp__demo__create_ticket", "mcp__demo__delete_item"
SEARCH = "mcp__demo__kb_search_431baf9c58bf"


def ctx(user: str = "alice") -> ToolContext:
    return ToolContext(workspace_id="ws1", session_id="s1", user_id=user)


async def register(env, stub, **over):
    body = McpServerCreate(name="demo", title="演示服务", url=stub.url, headers=AUTH, **over)
    return await env.mcp.admin.create("alice", "ws1", body)


async def test_delete_removes_the_servers_tool_grants(env, stub):
    """删除服务时同一事务删掉它的工具授权（单个工具与整个服务的），别的服务的不动（技术方案 5.2 节）。"""
    record = await register(env, stub)
    await env.access.grant("alice", "ws1", "user:carol", "tool:mcp__demo__*", "use")
    await env.access.grant("alice", "ws1", "user:dave", f"tool:{ADD}", "use")
    await env.access.grant("alice", "ws1", "user:dave", "tool:mcp__demox__add", "use")
    await env.mcp.admin.delete("alice", "ws1", record.id)
    assert [g.resource for g in await env.access.list_grants("alice", "ws1")] == ["tool:mcp__demox__add"]
    assert not env.access.check("carol", "ws1", f"tool:{TICKET}", "use")


async def test_register_fetches_tools_and_seals_headers(env, stub):
    record = await register(env, stub)
    assert [t.name for t in record.tools] == [ADD, TICKET, DELETE, SEARCH]
    assert [(t.name, t.risk) for t in record.tools if t.name in (ADD, TICKET, DELETE)] == [
        (ADD, "read"), (TICKET, "write"), (DELETE, "dangerous")]
    assert record.header_names == ({"name": "Authorization", "last4": "1234"},)
    admin = create_async_engine(env.admin_url)
    async with admin.connect() as conn:
        stored = await conn.scalar(text("SELECT headers_ciphertext FROM mcp_server"))
    await admin.dispose()
    assert TOKEN not in stored and "mcp-test-token" not in stored, "请求头只存密文"


async def test_only_workspace_editors_manage(env, stub):
    with pytest.raises(Forbidden):
        await env.mcp.admin.create("carol", "ws1", McpServerCreate(name="x", title="x", url=stub.url))
    with pytest.raises(Forbidden):
        await env.mcp.admin.list_servers("dave", "ws1")
    await register(env, stub)
    assert len(await env.mcp.admin.list_servers("bob", "ws1")) == 1


async def test_unreachable_or_duplicate_saves_nothing(env, stub):
    with pytest.raises(Unreachable, match="HTTP 401"):
        await env.mcp.admin.create("alice", "ws1", McpServerCreate(name="demo", title="演示", url=stub.url))
    assert await env.mcp.admin.list_servers("alice", "ws1") == []
    await register(env, stub)
    with pytest.raises(Conflict):
        await register(env, stub)


async def test_registry_lists_usable_tools_and_invokes(env, stub):
    record = await register(env, stub)
    registry = env.mcp.registry
    assert {s.name for s in await registry.available(ctx())} == {ADD, TICKET, DELETE, SEARCH}
    assert await registry.invoke(ctx(), ADD, {"a": 20, "b": 22}) == "42"
    assert await registry.invoke(ctx(), SEARCH, {"query": "合同"}) == "找到 3 条与「合同」相关的结果"
    with pytest.raises(ToolFailure, match="记录 x 不存在"):
        await registry.invoke(ctx(), DELETE, {"item_id": "x"})

    await env.mcp.admin.configure_tool("alice", "ws1", record.id, DELETE, McpToolPatch(enabled=False))
    assert DELETE not in {s.name for s in await registry.available(ctx())}
    with pytest.raises(ToolFailure, match="不存在或已停用"):
        await registry.invoke(ctx(), DELETE, {"item_id": "x"})
    await env.mcp.admin.update("alice", "ws1", record.id, McpServerPatch(enabled=False))
    assert await registry.available(ctx()) == [], "停用服务后它的工具立即不可用"


async def test_tool_settings_change_spec(env, stub):
    record = await register(env, stub)
    await env.mcp.admin.configure_tool("alice", "ws1", record.id, ADD, McpToolPatch(risk="write", approval="ask"))
    spec = next(s for s in await env.mcp.registry.available(ctx()) if s.name == ADD)
    assert (spec.risk, spec.approval_mode) == ("write", "ask")
    await env.mcp.admin.configure_tool("alice", "ws1", record.id, ADD, McpToolPatch(risk="auto", approval="auto"))
    spec = next(s for s in await env.mcp.registry.available(ctx()) if s.name == ADD)
    assert (spec.risk, spec.approval_mode) == ("read", "never")


async def test_failed_sync_keeps_tools_and_records_reason(env, stub):
    record = await register(env, stub)
    await stub.__aexit__(None, None, None)  # MCP 服务下线
    with pytest.raises(Unreachable, match="连不上"):
        await env.mcp.admin.sync("alice", "ws1", record.id)
    current = await env.mcp.admin.get("alice", "ws1", record.id)
    assert len(current.tools) == 4 and "连不上" in current.sync_error
    with pytest.raises(ToolFailure, match="演示服务：连不上"):
        await env.mcp.registry.invoke(ctx(), ADD, {"a": 1, "b": 1})


async def test_changing_headers_reconnects_first(env, stub):
    record = await register(env, stub)
    with pytest.raises(Unreachable, match="HTTP 401"):
        await env.mcp.admin.update("alice", "ws1", record.id, McpServerPatch(headers={"Authorization": "Bearer no"}))
    unchanged = await env.mcp.admin.get("alice", "ws1", record.id)
    assert unchanged.header_names == record.header_names
    renamed = await env.mcp.admin.update("alice", "ws1", record.id, McpServerPatch(title="新名字"))
    assert renamed.title == "新名字" and renamed.tools == record.tools


async def test_members_need_tool_grants(env, stub):
    await register(env, stub)
    builtin = frozenset({"kw_now"})
    registry = AuthorizedToolRegistry(env.mcp.registry, ToolClearance(env.access, builtin=builtin))
    assert len(await registry.available(ctx("bob"))) == 4, "admin 默认能用全部工具"
    assert await registry.available(ctx("carol")) == [], "编辑者默认不能用外部工具"
    await env.access.grant("alice", "ws1", "user:carol", "tool:mcp__demo__*", "use")
    assert len(await registry.available(ctx("carol"))) == 4, "按服务授权覆盖它的全部工具"
    await env.access.revoke("alice", "ws1", "user:carol", "tool:mcp__demo__*")
    await env.access.grant("alice", "ws1", "user:carol", f"tool:{ADD}", "use")
    assert [s.name for s in await registry.available(ctx("carol"))] == [ADD]
    with pytest.raises(ToolFailure):
        await registry.invoke(ctx("carol"), TICKET, {"title": "x"})
