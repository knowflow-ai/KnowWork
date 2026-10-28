"""人的操作：写请求一律记一条，结果分成功、拒绝、失败；读请求不记；操作者来自认证依赖登记的身份。"""

from typing import Annotated

import httpx
import pytest
from fastapi import Depends, FastAPI, HTTPException, Request

from knowwork_server.audit.actions import action_for
from knowwork_server.audit.context import annotate, current, note_principal
from knowwork_server.audit.middleware import AuditMiddleware, result_of
from knowwork_server.auth.principal import Principal

pytestmark = pytest.mark.anyio


def who(request: Request) -> Principal:
    kind = request.headers.get("x-test-auth")
    if kind == "key":
        principal = Principal(user_id="alice", auth_method="api_key", api_key_id="key_1", scopes=frozenset())
    elif kind == "session":
        principal = Principal(user_id="alice", auth_method="session", session_id="sess_1")
    else:
        raise HTTPException(status_code=401, detail="未登录")
    note_principal(principal)
    return principal


Who = Annotated[Principal, Depends(who)]


def make_app(sink) -> FastAPI:
    app = FastAPI()
    app.add_middleware(AuditMiddleware, sink=sink, default_workspace_id="default")

    @app.post("/api/workspaces/{workspace_id}/members", status_code=201)
    async def add(workspace_id: str, user: Who):
        annotate(resource_id="bob", role="admin", password="不能记")
        return {}

    @app.delete("/api/workspaces/{workspace_id}/grants", status_code=204)
    async def revoke(workspace_id: str, user: Who, subject: str, resource: str, secret: str = ""):
        if subject == "user:nobody":
            raise HTTPException(status_code=403, detail="无权限")

    @app.patch("/api/workspaces/{workspace_id}/models/{model_id}")
    async def broken(workspace_id: str, model_id: str, user: Who):
        raise RuntimeError("数据库挂了")

    @app.post("/api/workspaces/{workspace_id}/conversations/{conversation_id}/approvals/{approval_id}")
    async def decide(conversation_id: str, approval_id: str, user: Who):
        annotate(allow=False)
        return {}

    @app.post("/api/widgets/{widget_id}")
    async def unregistered(widget_id: str):
        return {}

    @app.get("/api/things")
    async def read(user: Who):
        return {"seen": current() is not None}

    @app.get("/api/auth/oidc/callback")
    async def callback():
        annotate(record=True, actor_id="carol")
        return {}

    return app


@pytest.fixture
async def client(memory_sink):
    transport = httpx.ASGITransport(app=make_app(memory_sink), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://kw") as http:
        yield http


async def test_successful_write_records_actor_credential_and_resource(client, memory_sink):
    response = await client.post("/api/workspaces/ws1/members", headers={"x-test-auth": "key", "x-request-id": "rq-1"})
    assert response.status_code == 201 and response.headers["x-request-id"] == "rq-1"
    [event] = memory_sink.events
    assert (event.workspace_id, event.actor_type, event.actor_id) == ("ws1", "human", "alice")
    assert (event.credential_type, event.credential_id) == ("api_key", "key_1")
    assert (event.action, event.resource_type, event.resource_id) == ("member.add", "member", "bob")
    assert (event.result, event.status_code, event.request_id, event.ip) == ("success", 201, "rq-1", "127.0.0.1")
    assert event.normalized().detail == {"method": "POST", "route": "/api/workspaces/{workspace_id}/members",
                                         "role": "admin"}


async def test_denied_and_unauthenticated_writes_are_recorded_as_denied(client, memory_sink):
    await client.delete("/api/workspaces/ws1/grants", params={"subject": "user:nobody", "resource": "skill:s1",
                                                              "secret": "x"}, headers={"x-test-auth": "session"})
    await client.post("/api/workspaces/ws1/members")
    denied, anonymous = memory_sink.events
    assert (denied.action, denied.result, denied.status_code) == ("grant.revoke", "denied", 403)
    assert (denied.credential_type, denied.credential_id) == ("session", "sess_1")
    assert denied.detail["subject"] == "user:nobody" and "secret" not in denied.detail
    assert (anonymous.result, anonymous.status_code, anonymous.actor_id) == ("denied", 401, None)


async def test_errors_are_recorded_as_failed(client, memory_sink):
    response = await client.patch("/api/workspaces/ws1/models/m1", headers={"x-test-auth": "session"})
    assert response.status_code == 500
    [event] = memory_sink.events
    assert (event.action, event.resource_id, event.result, event.status_code) == ("model.update", "m1", "failed", 500)


async def test_approval_decision_carries_the_choice_and_conversation(client, memory_sink):
    await client.post("/api/workspaces/ws1/conversations/c1/approvals/ap_1", headers={"x-test-auth": "session"})
    [event] = memory_sink.events
    assert (event.action, event.resource_id, event.session_id) == ("approval.decide", "ap_1", "c1")
    assert event.detail["allow"] is False and event.workspace_id == "ws1", "空间从路径上取"


async def test_reads_are_not_recorded_unless_marked(client, memory_sink):
    response = await client.get("/api/things", headers={"x-test-auth": "session"})
    assert response.json() == {"seen": True}
    assert memory_sink.events == []
    await client.get("/api/auth/oidc/callback")
    [event] = memory_sink.events
    assert (event.action, event.actor_id, event.result) == ("auth.oidc_login", "carol", "success")


async def test_unmatched_paths_are_skipped_and_unregistered_routes_get_a_derived_action(client, memory_sink):
    await client.post("/nowhere")
    await client.post("/api/widgets/w1", headers={"x-request-id": "bad id with spaces"})
    [event] = memory_sink.events
    assert (event.action, event.resource_type, event.resource_id) == ("widgets.create", "widgets", "w1")
    assert event.request_id != "bad id with spaces" and len(event.request_id) == 32


async def test_sink_failure_does_not_change_the_response(caplog):
    class Boom:
        async def record(self, event):
            raise RuntimeError("down")

    transport = httpx.ASGITransport(app=make_app(Boom()))
    async with httpx.AsyncClient(transport=transport, base_url="http://kw") as http:
        response = await http.post("/api/workspaces/ws1/members", headers={"x-test-auth": "session"})
    assert response.status_code == 201 and "audit sink failed" in caplog.text


async def test_non_http_scopes_pass_through(memory_sink):
    seen = []

    async def inner(scope, receive, send):
        seen.append(scope["type"])

    await AuditMiddleware(inner, sink=memory_sink, default_workspace_id="d")({"type": "lifespan"}, None, None)
    assert seen == ["lifespan"] and memory_sink.events == []


def test_result_mapping_and_action_derivation():
    assert [result_of(s) for s in (200, 302, 401, 403, 429, 404, 422, 500)] == [
        "success", "success", "denied", "denied", "denied", "failed", "failed", "failed"]
    assert action_for("DELETE", "/api/auth/api-keys/{key_id}").action == "api_key.revoke"
    derived = action_for("PUT", "/api/workspaces/{workspace_id}/skills/{skill_id}/releases")
    assert (derived.action, derived.id_param) == ("releases.update", None)
    assert action_for("POST", "/").action == "unknown.create"


async def test_annotate_without_middleware_is_a_no_op():
    annotate(role="x")
    note_principal(Principal(user_id="u", auth_method="session"))
    assert current() is None
