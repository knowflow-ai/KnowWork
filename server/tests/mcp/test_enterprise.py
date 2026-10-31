"""企业 MCP（代码里叫 enterprise，存在平台配置所在的默认空间名下）：只有系统管理员登记，名字带 ent_ 前缀、不和任何空间服务撞名；
发给选定或全部空间，发到的空间默认全体成员可用，空间管理员可以停用、收窄给谁用；默认空间自己的服务列表里看不到它；
每人的连接按人存，换空间不用重连，被移出某个空间也不删。"""

import pytest
from sqlalchemy import select, text

from knowwork_server.profile.clearance import ENTERPRISE_MCP_PREFIX, ToolClearance
from knowwork_server.tools.mcp.errors import Forbidden, InvalidInput, NotFound, Unreachable
from knowwork_server.tools.mcp.models import McpConnectionRow
from knowwork_server.tools.mcp.naming import is_enterprise_tool
from knowwork_server.tools.mcp.schemas import McpServerCreate, McpToolPatch, TokenConfig
from knowwork_server.tools.registry import ToolContext

from .conftest import AUTH
from .test_oauth import authorize, ready

pytestmark = pytest.mark.anyio

ROOT = "root"  # 系统管理员
GOOD = "mcp-test-token-1234"  # 桩服务只认 Bearer 加这个令牌
ADD = "mcp__ent_demo__add"


def ctx(user: str, workspace: str = "ws1") -> ToolContext:
    return ToolContext(workspace_id=workspace, session_id="s1", user_id=user)


@pytest.fixture
async def enterprise(env):
    await env.access.create_workspace(workspace_id="default", name="默认空间", owner_user_id="alice")
    env.access.set_system_admin(ROOT)
    return env.mcp.enterprise


async def register(enterprise, stub, *, audience=("ws1",), **kwargs):
    body = McpServerCreate(name="demo", title="演示服务", url=stub.url, headers=AUTH, **kwargs)
    record = await enterprise.create(ROOT, body)
    return await enterprise.set_audience(ROOT, record.id, list(audience)) if audience else record


async def names(env, user: str, workspace: str = "ws1") -> set[str]:
    return {spec.name for spec in await env.mcp.registry.available(ctx(user, workspace))}


async def test_only_the_system_admin_registers_and_names_get_their_own_prefix(env, stub, enterprise):
    with pytest.raises(Forbidden):
        await enterprise.create("alice", McpServerCreate(name="demo", title="演示服务", url=stub.url, headers=AUTH))
    record = await register(enterprise, stub, audience=())
    assert (record.name, record.scope, record.audience) == ("ent_demo", "enterprise", ())
    assert ADD in {tool.name for tool in record.tools}
    assert [s.id for s in await enterprise.list_servers(ROOT)] == [record.id]
    # 存在默认空间名下，但默认空间自己的服务列表、详情都碰不到它
    assert await env.mcp.admin.list_servers("alice", "default") == []
    with pytest.raises(NotFound):
        await env.mcp.admin.get("alice", "default", record.id)
    with pytest.raises(NotFound):
        await enterprise.get(ROOT, "mcp_nope")
    with pytest.raises(InvalidInput):
        await enterprise.set_audience(ROOT, record.id, ["nowhere"])


async def test_reaches_its_audience_and_everyone_there_can_use_it(env, stub, enterprise):
    record = await register(enterprise, stub, audience=())
    assert ADD not in await names(env, "carol"), "还没发给任何空间"
    await enterprise.set_audience(ROOT, record.id, ["ws1"])
    assert ADD in await names(env, "carol"), "发到的空间里成员不用另外授权"
    assert await env.mcp.registry.invoke(ctx("carol"), ADD, {"a": 1, "b": 2}) == "3"
    [row] = await env.mcp.me.list("carol", "ws1")
    assert (row.server_id, row.status) == (record.id, "ready")
    clearance = ToolClearance(env.access, frozenset())
    assert clearance.permits("carol", "ws1", ADD), "企业 MCP 工具给不给由 MCP 模块判，这一层不再要 tool 授权"
    assert is_enterprise_tool(ADD) and ADD.startswith(ENTERPRISE_MCP_PREFIX), "两处的前缀要一致"
    assert not clearance.permits("carol", "ws1", "mcp__demo__add"), "空间服务的工具照旧要 tool 授权"
    await env.access.create_workspace(workspace_id="ws2", name="空间二", owner_user_id="alice")
    assert ADD not in await names(env, "alice", "ws2")
    await enterprise.set_audience(ROOT, record.id, ["*"])
    assert ADD in await names(env, "alice", "ws2"), "全部空间：以后新建的也有"


async def test_workspace_admin_turns_it_off_or_narrows_who_uses_it_here(env, stub, enterprise):
    record = await register(enterprise, stub, audience=("*",))
    await env.access.create_workspace(workspace_id="ws2", name="空间二", owner_user_id="alice")
    here = env.mcp.here
    [row] = await here.list("bob", "ws1")
    assert (row.server_id, row.off, row.narrowed, row.resource) == (record.id, False, False, "tool:mcp__ent_demo__*")
    with pytest.raises(Forbidden):
        await here.set_off("carol", "ws1", record.id, True)
    with pytest.raises(NotFound):
        await here.set_off("bob", "ws1", "mcp_nope", True)

    await here.set_off("bob", "ws1", record.id, True)
    assert ADD not in await names(env, "carol")
    assert ADD in await names(env, "alice", "ws2"), "只停用本空间"
    assert await env.mcp.me.list("carol", "ws1") == []
    await here.set_off("bob", "ws1", record.id, False)

    assert await here.set_users("bob", "ws1", record.id, ["user:dave"]) is True
    assert ADD not in await names(env, "carol")
    assert ADD in await names(env, "dave")
    assert ADD in await names(env, "bob"), "管理员本来就能用全部"
    assert await here.set_users("bob", "ws1", record.id, ["role:editor"]) is False
    assert ADD in await names(env, "carol")


async def test_a_personal_connection_follows_the_person_across_workspaces(env, stub, enterprise):
    body = McpServerCreate(name="demo", title="演示服务", url=stub.url, auth="user_token",
                           token_config=TokenConfig(), token=GOOD)
    record = await enterprise.set_audience(ROOT, (await enterprise.create(ROOT, body)).id, ["*"])
    await env.access.create_workspace(workspace_id="ws2", name="空间二", owner_user_id="alice")
    await env.access.add_member("alice", "ws2", "carol", "editor")
    assert ADD not in await names(env, "carol"), "没连接的不进对话"
    await env.mcp.me.connect("carol", "ws1", record.id, GOOD)
    [row] = await env.mcp.me.list("carol", "ws2")
    assert row.status == "connected", "换空间不用重连"
    assert await env.mcp.registry.invoke(ctx("carol", "ws2"), ADD, {"a": 2, "b": 3}) == "5"

    env.access.on_member_removed(env.mcp.connections.forget_member)
    await env.access.remove_member("alice", "ws1", "carol")
    [row] = await env.mcp.me.list("carol", "ws2")
    assert row.status == "connected", "被移出一个空间不删企业服务的连接"
    await env.mcp.me.disconnect("carol", "ws2", record.id)
    async with env.sessionmaker() as session, session.begin():
        await session.execute(text("SELECT set_config('app.workspace_id', 'default', true)"))
        users = (await session.scalars(select(McpConnectionRow.user_id))).all()
    assert users == [ROOT], "企业服务的连接存在默认空间名下"


async def test_deleting_it_removes_the_workspace_settings_and_everyones_connections(env, stub, enterprise):
    record = await register(enterprise, stub, audience=("*",))
    await env.mcp.here.set_off("bob", "ws1", record.id, True)
    await enterprise.delete(ROOT, record.id)
    assert await env.mcp.here.list("bob", "ws1") == []
    assert ADD not in await names(env, "carol")


async def test_promote_carries_the_tool_settings_but_not_the_credentials(env, stub, enterprise):
    source = await env.mcp.admin.create("alice", "ws1", McpServerCreate(name="demo", title="演示服务", url=stub.url,
                                                                         headers=AUTH))
    await env.mcp.admin.configure_tool("alice", "ws1", source.id, "mcp__demo__add", McpToolPatch(policy="block"))
    assert [(w.id, s.id) for w, s in await enterprise.promotable(ROOT)] == [("ws1", source.id)]
    body = McpServerCreate(name="demo", title="演示服务", url=stub.url, headers=AUTH)
    with pytest.raises(Forbidden):
        await enterprise.promote("alice", "ws1", source.id, body)
    with pytest.raises(Unreachable, match="401"):  # 共享凭据不跟着上去，要系统管理员重填
        await enterprise.promote(ROOT, "ws1", source.id, McpServerCreate(name="demo", title="演示服务", url=stub.url))
    record = await enterprise.promote(ROOT, "ws1", source.id, body)
    assert (record.name, record.scope, record.audience) == ("ent_demo", "enterprise", ())
    assert {tool.raw_name: tool.policy for tool in record.tools}["add"] == "block", "每个工具的三档跟着上去"
    assert ADD in {tool.name for tool in record.tools}
    assert (await env.mcp.admin.get("alice", "ws1", source.id)).name == "demo", "原来那份不动"
    assert await env.mcp.admin.promoted("ws1") == {source.id: "演示服务"}, "那个空间看得到已经收为企业 MCP，好去删掉原来那份"
    assert record.origin_server_id == source.id
    with pytest.raises(NotFound):  # 企业服务不能再收一次
        await enterprise.promote(ROOT, "default", record.id, body)
    with pytest.raises(NotFound):
        await enterprise.promote(ROOT, "ws1", "mcp_nope", body)


async def test_promote_an_oauth_service_keeps_the_settings_through_the_first_login(env, oauth_stub, enterprise):
    source = await ready(env, oauth_stub)
    await env.mcp.admin.configure_tool("alice", "ws1", source.id, "mcp__tickets__add", McpToolPatch(policy="block"))
    body = McpServerCreate(name="tickets", title="工单系统", url=oauth_stub.url, auth="oauth")
    record = await enterprise.promote(ROOT, "ws1", source.id, body)
    assert {tool.raw_name: tool.policy for tool in record.tools}["add"] == "block", "登录前先带上来源的设置"
    assert record.synced_at is None
    url = await env.mcp.me.login(ROOT, "ws1", record.id, "/admin/tools?tab=enterprise")
    params = await authorize(oauth_stub, url, as_who="root@tickets")
    await env.mcp.login.complete(ROOT, state=params["state"], code=params["code"], iss=params.get("iss"), error=None)
    synced = await enterprise.get(ROOT, record.id)
    assert synced.synced_at is not None
    assert {tool.raw_name: tool.policy for tool in synced.tools}["add"] == "block", "登录后拉第一份清单时保留"
