"""「设置 → 连接」的接口：本人列出、连接、断开；令牌不回显；不接受 API Key。"""

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request

from knowwork_server.auth.principal import Principal
from knowwork_server.tools.mcp.api import build_me_router
from knowwork_server.tools.mcp.schemas import McpServerCreate

pytestmark = pytest.mark.anyio
GOOD = "mcp-test-token-1234"
BASE = "/api/workspaces/ws1/me/mcp-connections"


async def principal(request: Request) -> Principal:
    user = request.headers.get("x-user")
    if not user:
        raise HTTPException(status_code=401)
    if request.headers.get("x-api-key"):
        return Principal(user_id=user, auth_method="api_key", scopes=frozenset({"tools"}))
    return Principal(user_id=user, auth_method="session")


async def test_connect_list_disconnect_over_http(env, stub):
    record = await env.mcp.admin.create("alice", "ws1", McpServerCreate(
        name="demo", title="演示服务", url=stub.url, auth="user_token", token=GOOD))
    app = FastAPI()
    app.include_router(build_me_router(env.mcp.me, principal))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as http:
        listed = (await http.get(BASE, headers={"x-user": "carol"})).json()
        assert [(r["serverId"], r["status"]) for r in listed] == [(record.id, "none")]
        wrong = await http.put(f"{BASE}/{record.id}", json={"token": "wrong"}, headers={"x-user": "carol"})
        assert wrong.status_code == 400 and "拒绝了这份令牌" in wrong.json()["detail"]
        done = await http.put(f"{BASE}/{record.id}", json={"token": GOOD}, headers={"x-user": "carol"})
        assert done.status_code == 200 and done.json()["status"] == "connected" and GOOD not in done.text
        by_key = await http.get(BASE, headers={"x-user": "carol", "x-api-key": "1"})
        assert by_key.status_code == 403, "凭据只能本人在页面上管"
        assert (await http.get(BASE, headers={"x-user": "mallory"})).status_code == 403
        assert (await http.delete(f"{BASE}/{record.id}", headers={"x-user": "carol"})).status_code == 204
        assert (await http.delete(f"{BASE}/{record.id}", headers={"x-user": "carol"})).status_code == 404


async def test_login_and_callback_over_http(env, oauth_stub):
    """点「连接」拿授权地址；对方跳回回调：本人会话才认，成功或失败都跳回发起的页面并带上结果。"""
    from urllib.parse import parse_qs, urlparse

    from knowwork_server.tools.mcp.api import build_oauth_router
    from knowwork_server.tools.mcp.schemas import McpServerCreate as Create

    record = await env.mcp.admin.create("alice", "ws1", Create(name="tickets", title="工单系统", url=oauth_stub.url,
                                                               auth="oauth"))
    app = FastAPI()
    app.include_router(build_me_router(env.mcp.me, principal))
    app.include_router(build_oauth_router(env.mcp.login, principal))
    alice = {"x-user": "alice"}

    async def go_to_provider(http, user: dict) -> str:
        started = await http.post(f"{BASE}/{record.id}/login", json={"returnTo": "/admin/tools?server=x"},
                                  headers=user)
        assert started.status_code == 200, started.text
        async with httpx.AsyncClient() as outside:
            back = await outside.get(started.json()["authorizeUrl"], follow_redirects=False)
        return urlparse(back.headers["location"]).query

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as http:
        bad = await http.post(f"{BASE}/{record.id}/login", json={"returnTo": "//evil.example"}, headers=alice)
        assert bad.status_code == 422, "只许回到站内页面"
        by_key = await http.post(f"{BASE}/{record.id}/login", json={}, headers={**alice, "x-api-key": "1"})
        assert by_key.status_code == 403

        query = await go_to_provider(http, alice)
        anonymous = await http.get(f"/api/mcp/oauth/callback?{query}")
        assert anonymous.status_code == 302 and "mcpError" in anonymous.headers["location"]
        query = await go_to_provider(http, alice)
        stranger = await http.get(f"/api/mcp/oauth/callback?{query}", headers={"x-user": "carol"})
        target = urlparse(stranger.headers["location"])
        # 不是发起人：回他自己的「设置 → 连接」，不去发起人的页面
        assert (target.path, "不是你发起的" in parse_qs(target.query)["mcpError"][0]) == ("/settings", True)

        query = await go_to_provider(http, alice)
        done = await http.get(f"/api/mcp/oauth/callback?{query}", headers=alice)
        assert done.status_code == 302
        assert done.headers["location"] == f"/admin/tools?server=x&mcpConnected={record.id}"
        assert "code=" not in done.headers["location"], "授权码不回传给页面"
    assert len((await env.mcp.admin.get("alice", "ws1", record.id)).tools) == 4
