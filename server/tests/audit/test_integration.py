"""装配后的整条链路：真实身份与权限路由挂上审计中间件，登录、API Key、成员与授权变更都能查到。"""

import httpx
import pytest

from knowwork_server.access.api import build_router as access_router
from knowwork_server.audit import AUDIT_SCOPE, build_audit, build_router
from knowwork_server.identity import IdentityConfig, build_identity
from knowwork_server.identity.api import build_router as identity_router
from knowwork_server.principals import WORKSPACE_SCOPE, scoped_principal

pytestmark = pytest.mark.anyio
PASSWORD = "correct horse battery"


@pytest.fixture
async def env(app_sessionmaker, access):
    from fastapi import FastAPI

    identity = build_identity(app_sessionmaker, IdentityConfig(public_base_url="https://kw.test"))
    owner = await identity.users.create_user(email="owner@corp.com", name="Owner", password=PASSWORD)
    await access.create_workspace(workspace_id="ws3", name="空间三", owner_user_id=owner.id)
    audit = build_audit(app_sessionmaker, default_workspace_id="ws3")
    audit.start()
    app = FastAPI()
    app.include_router(identity_router(identity, can_bind_workspace=lambda who, ws: True))
    app.include_router(access_router(access, scoped_principal(identity, WORKSPACE_SCOPE)))
    app.include_router(build_router(audit.query, access, scoped_principal(identity, AUDIT_SCOPE)))
    audit.install(app)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://kw.test") as http:
        yield {"http": http, "audit": audit, "owner": owner}
    await audit.close()


async def test_human_operations_are_queryable(env):
    http, audit, owner = env["http"], env["audit"], env["owner"]
    assert (await http.post("/api/auth/login", json={"email": "owner@corp.com", "password": "wrong"})).status_code == 401
    assert (await http.post("/api/auth/login", json={"email": "Owner@corp.com", "password": PASSWORD})).status_code == 200
    created = (await http.post("/api/auth/api-keys", json={"name": "审计", "scopes": [AUDIT_SCOPE]})).json()
    added = await http.post("/api/workspaces/ws3/members", json={"user_id": "bob", "role": "viewer"})
    assert added.status_code == 201
    await http.post("/api/workspaces/ws3/grants", json={"subject": "user:bob", "resource": "skill:s1", "action": "use"})
    assert (await http.delete(f"/api/auth/api-keys/{created['id']}")).status_code == 204
    await audit.writer.flush()

    http.cookies.clear()
    key = (await http.post("/api/auth/login", json={"email": "owner@corp.com", "password": PASSWORD}))
    other = (await http.post("/api/auth/api-keys", json={"name": "读", "scopes": [AUDIT_SCOPE]})).json()["key"]
    http.cookies.clear()
    await audit.writer.flush()
    response = await http.get("/api/workspaces/ws3/audit-logs", headers={"authorization": f"Bearer {other}"})
    assert key.status_code == 200 and response.status_code == 200
    items = {(i["action"], i["result"]): i for i in response.json()["items"]}
    failed = items[("auth.login", "denied")]
    assert failed["actorId"] is None and failed["detail"]["account"] == "owner@corp.com"
    assert items[("auth.login", "success")]["actorId"] == owner.id
    made = items[("api_key.create", "success")]
    assert made["resourceId"] == created["id"] and made["credentialType"] == "session" and made["credentialId"]
    assert created["key"] not in str(response.json())  # 明文 Key 绝不进审计
    assert items[("member.add", "success")]["detail"]["role"] == "viewer"
    assert items[("grant.create", "success")]["detail"]["subject"] == "user:bob"
    assert items[("api_key.revoke", "success")]["resourceId"] == created["id"]
    assert all(i["actorType"] == "human" for i in response.json()["items"])
    assert PASSWORD not in str(response.json())


async def test_api_key_without_audit_scope_cannot_read(env):
    http = env["http"]
    await http.post("/api/auth/login", json={"email": "owner@corp.com", "password": PASSWORD})
    key = (await http.post("/api/auth/api-keys", json={"name": "无审计", "scopes": [WORKSPACE_SCOPE]})).json()["key"]
    http.cookies.clear()
    response = await http.get("/api/workspaces/ws3/audit-logs", headers={"authorization": f"Bearer {key}"})
    assert response.status_code == 403


async def test_extra_sinks_and_purge_entry(app_sessionmaker, access, memory_sink):
    from knowwork_server.audit import AuditEvent, FanoutSink

    audit = build_audit(app_sessionmaker, default_workspace_id="ws1", extra_sinks=(memory_sink,))
    assert isinstance(audit.sink, FanoutSink)
    audit.start()
    await audit.sink.record(AuditEvent(workspace_id="ws1", actor_type="system", action="setup.initialize",
                                       result="success"))
    await audit.writer.flush()
    report = await audit.purge(retention_days=1)
    await audit.close()
    assert [e.action for e in memory_sink.events] == ["setup.initialize"]
    assert report.total == 0 and set(report.deleted) >= {"ws1", "ws2"}


def test_lazy_exports():
    from knowwork_server import audit

    with pytest.raises(AttributeError):
        audit.nothing_here  # noqa: B018
