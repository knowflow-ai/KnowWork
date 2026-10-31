"""OAuth 授权的 MCP 服务：登记时发现与注册、登记人登录后拉第一份工具清单；成员跳去对方登录、回调存成本人的连接；
state 只能用一次、只认发起人；快过期先刷新且并发只刷新一次；对方吊销后标成失效、不回落；认证方式变了连接作废。"""

import asyncio
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from knowwork_server.tools.mcp.errors import InvalidInput
from knowwork_server.tools.mcp.oauth_login import LoginFailed
from knowwork_server.tools.mcp.schemas import McpServerCreate, McpServerPatch, OAuthInput
from knowwork_server.tools.registry import ToolContext, ToolFailure

from ..mcp_oauth_stub import MANUAL_CLIENT, McpOAuthStub
from ..mcp_stub import McpStub
from ..support import free_port
from .conftest import PUBLIC

pytestmark = pytest.mark.anyio

ADD = "mcp__tickets__add"
CALLBACK = f"{PUBLIC}/api/mcp/oauth/callback"


def ctx(user: str) -> ToolContext:
    return ToolContext(workspace_id="ws1", session_id="s1", user_id=user)


async def register(env, oauth_stub, **oauth):
    body = McpServerCreate(name="tickets", title="工单系统", url=oauth_stub.url, auth="oauth",
                           oauth=OAuthInput(**oauth) if oauth else None)
    return await env.mcp.admin.create("alice", "ws1", body)


async def authorize(oauth_stub, url: str, *, as_who: str) -> dict[str, str]:
    """在对方页面登录：照授权地址走一次，拿回调里的参数。"""
    oauth_stub.state.login_as = as_who
    async with httpx.AsyncClient() as http:
        response = await http.get(url, follow_redirects=False)
    assert response.status_code == 302, response.text
    location = response.headers["location"]
    assert location.startswith(CALLBACK)
    return {key: values[0] for key, values in parse_qs(urlparse(location).query).items()}


async def log_in(env, oauth_stub, user: str, server_id: str):
    url = await env.mcp.me.login(user, "ws1", server_id, "/settings?tab=connections")
    params = await authorize(oauth_stub, url, as_who=f"{user}@tickets")
    return await env.mcp.login.complete(user, state=params["state"], code=params["code"], iss=params.get("iss"),
                                        error=None)


async def ready(env, oauth_stub):
    """登记并由登记人登录一次（拉到第一份工具清单）。"""
    record = await register(env, oauth_stub)
    await log_in(env, oauth_stub, "alice", record.id)
    return await env.mcp.admin.get("alice", "ws1", record.id)


async def test_probe_tells_whether_the_service_uses_oauth(env, oauth_stub, stub):
    found = await env.mcp.admin.probe("alice", "ws1", oauth_stub.url)
    assert (found.oauth, found.dynamic_registration, found.scope) == (True, True, "tickets:read")
    assert found.redirect_uri == CALLBACK
    assert found.needs_credentials
    plain = await env.mcp.admin.probe("alice", "ws1", stub.url)
    assert (plain.oauth, plain.needs_credentials) == (False, True), "只认个人令牌的服务：要凭据，但不是 OAuth"
    async with McpStub(free_port(), token="") as open_stub:  # 公开的服务（例如文档检索）不要任何凭据
        public = await env.mcp.admin.probe("alice", "ws1", open_stub.url)
    assert (public.oauth, public.needs_credentials) == (False, False)
    oauth_stub.state.anonymous = True  # 不带凭据能用、另外支持 OAuth：默认无需认证，授权可选
    optional = await env.mcp.admin.probe("alice", "ws1", oauth_stub.url)
    assert (optional.oauth, optional.needs_credentials) == (True, False)


async def test_register_then_the_registrant_logs_in_and_the_first_tool_list_is_pulled(env, oauth_stub):
    record = await register(env, oauth_stub)
    assert (record.auth, record.tools, record.synced_at) == ("oauth", (), None)
    assert record.auth_config["dynamic"] and record.auth_config["client_id"].startswith("dyn-")
    issued_secret = oauth_stub.state.clients[record.auth_config["client_id"]][0]
    assert issued_secret not in str(record.auth_config), "client secret 只在密文里"
    assert await env.mcp.me.list("carol", "ws1") == [], "还没有工具清单，成员看不到"
    with pytest.raises(Exception, match="没有这个要授权的服务"):
        await env.mcp.me.login("carol", "ws1", record.id, "/settings")
    done = await log_in(env, oauth_stub, "alice", record.id)
    assert done.return_to == "/settings?tab=connections"
    synced = await env.mcp.admin.get("alice", "ws1", record.id)
    assert len(synced.tools) == 4 and synced.synced_at is not None
    assert {t.raw_name: t.policy for t in synced.tools}["add"] == "allow", "第一份清单照对方注解预填"
    assert await env.mcp.registry.invoke(ctx("alice"), ADD, {"a": 1, "b": 2}) == "3"
    assert oauth_stub.state.callers[-1] == "alice@tickets"


async def test_members_log_in_with_their_own_account(env, oauth_stub):
    record = await ready(env, oauth_stub)
    [row] = await env.mcp.me.list("carol", "ws1")
    assert (row.status, row.auth) == ("none", "oauth")
    assert await env.mcp.registry.available(ctx("carol")) == [], "没登录的不进对话"
    with pytest.raises(InvalidInput, match="用 OAuth 授权"):
        await env.mcp.me.connect("carol", "ws1", record.id, "some-token")
    await log_in(env, oauth_stub, "carol", record.id)
    [row] = await env.mcp.me.list("carol", "ws1")
    assert row.status == "connected"
    assert await env.mcp.registry.invoke(ctx("carol"), ADD, {"a": 2, "b": 2}) == "4"
    assert oauth_stub.state.callers[-1] == "carol@tickets", "按本人的账号调用"


async def test_state_is_single_use_and_only_for_the_person_who_started_it(env, oauth_stub):
    record = await ready(env, oauth_stub)
    url = await env.mcp.me.login("carol", "ws1", record.id, "/settings?tab=connections")
    params = await authorize(oauth_stub, url, as_who="carol@tickets")
    with pytest.raises(LoginFailed, match="不是你发起的"):
        await env.mcp.login.complete("dave", state=params["state"], code=params["code"], iss=params["iss"], error=None)
    with pytest.raises(LoginFailed, match="已经用过或过期"):
        await env.mcp.login.complete("carol", state=params["state"], code=params["code"], iss=params["iss"],
                                     error=None)
    url = await env.mcp.me.login("carol", "ws1", record.id, "/settings?tab=connections")
    params = await authorize(oauth_stub, url, as_who="carol@tickets")
    with pytest.raises(LoginFailed, match="别的授权服务器") as failed:
        await env.mcp.login.complete("carol", state=params["state"], code=params["code"], iss="https://evil.example",
                                     error=None)
    assert failed.value.return_to == "/settings?tab=connections"
    url = await env.mcp.me.login("carol", "ws1", record.id, "/settings?tab=connections")
    state = parse_qs(urlparse(url).query)["state"][0]
    with pytest.raises(LoginFailed, match="取消了授权"):
        await env.mcp.login.complete("carol", state=state, code="", iss=None, error="access_denied")
    with pytest.raises(LoginFailed, match="授权状态不对"):
        await env.mcp.login.complete("carol", state="no-separator", code="x", iss=None, error=None)
    assert (await env.mcp.me.list("carol", "ws1"))[0].status == "none"


async def test_expiring_tokens_are_refreshed_once_even_under_concurrent_calls(env, oauth_stub):
    record = await ready(env, oauth_stub)
    oauth_stub.state.expires_in = 30  # 不到一分钟就过期：下次调用前先刷新
    await log_in(env, oauth_stub, "carol", record.id)
    oauth_stub.state.expires_in = 3600
    results = await asyncio.gather(*(env.mcp.registry.invoke(ctx("carol"), ADD, {"a": i, "b": 1}) for i in range(3)))
    assert results == ["1", "2", "3"]
    assert oauth_stub.state.refreshes == 1, "轮换式刷新令牌只能用一次，并发调用要排队等同一次刷新"


async def test_a_rejected_access_token_is_renewed_once_with_the_refresh_token(env, oauth_stub):
    """对方提前作废了访问令牌（或没给过期时间、我们不知道它过期了），刷新令牌还有效：换一张接着用，不标失效。"""
    record = await ready(env, oauth_stub)
    await log_in(env, oauth_stub, "carol", record.id)
    oauth_stub.state.drop_access("carol@tickets")
    assert await env.mcp.registry.invoke(ctx("carol"), ADD, {"a": 1, "b": 2}) == "3"
    assert oauth_stub.state.refreshes == 1
    assert (await env.mcp.me.list("carol", "ws1"))[0].status == "connected"


async def test_revoked_login_is_marked_invalid_and_never_falls_back(env, oauth_stub):
    record = await ready(env, oauth_stub)
    await log_in(env, oauth_stub, "carol", record.id)
    oauth_stub.state.revoke("carol@tickets")
    with pytest.raises(ToolFailure, match="连接失效了"):
        await env.mcp.registry.invoke(ctx("carol"), ADD, {"a": 1, "b": 2})
    assert (await env.mcp.me.list("carol", "ws1"))[0].status == "invalid"
    with pytest.raises(ToolFailure, match="还没连接或连接已失效"):
        await env.mcp.registry.invoke(ctx("carol"), ADD, {"a": 1, "b": 2})
    assert await env.mcp.registry.invoke(ctx("alice"), ADD, {"a": 1, "b": 2}) == "3", "别人的连接不受影响"
    # 刷新令牌被拒（例如对方吊销了授权）也算失效
    oauth_stub.state.expires_in = 30
    await log_in(env, oauth_stub, "dave", record.id)
    oauth_stub.state.revoke("dave@tickets")
    with pytest.raises(ToolFailure, match="还没连接或连接已失效"):
        await env.mcp.registry.invoke(ctx("dave"), ADD, {"a": 1, "b": 2})
    assert (await env.mcp.me.list("dave", "ws1"))[0].status == "invalid"


async def test_manual_client_when_the_service_does_not_support_registration(env):
    async with McpOAuthStub(free_port(), dynamic=False) as manual_stub:
        with pytest.raises(InvalidInput, match=CALLBACK):
            await register(env, manual_stub)
        manual_stub.add_manual_client(CALLBACK)
        record = await register(env, manual_stub, client_id=MANUAL_CLIENT[0], client_secret=MANUAL_CLIENT[1],
                                scope="tickets:read")
        assert (record.auth_config["dynamic"], record.auth_config["client_id"]) == (False, "kw-manual")
        await log_in(env, manual_stub, "alice", record.id)
        assert await env.mcp.registry.invoke(ctx("alice"), ADD, {"a": 1, "b": 1}) == "2"
        # 改 scope 不影响已有连接；同一个 client_id 不填 secret 表示沿用原来的
        await env.mcp.admin.update("alice", "ws1", record.id, McpServerPatch(
            oauth=OAuthInput(client_id=MANUAL_CLIENT[0], scope="tickets:read tickets:write")))
        assert (await env.mcp.me.list("alice", "ws1"))[0].status == "connected"


async def test_changing_the_auth_method_drops_old_connections(env, oauth_stub, stub):
    record = await ready(env, oauth_stub)
    await log_in(env, oauth_stub, "carol", record.id)
    await env.mcp.admin.update("alice", "ws1", record.id, McpServerPatch(url=stub.url, auth="user_token",
                                                                         token="mcp-test-token-1234"))
    mine = await env.mcp.connections.for_user("ws1", "carol")
    assert mine == {}, "换了认证方式，原来的登录作废"
    switched = await env.mcp.admin.get("alice", "ws1", record.id)
    assert (switched.auth, switched.oauth_client_ciphertext) == ("user_token", None)
    assert (await env.mcp.connections.for_user("ws1", "alice"))[record.id].kind == "token"
