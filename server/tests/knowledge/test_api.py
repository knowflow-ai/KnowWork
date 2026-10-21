import httpx
import pytest
from fastapi import FastAPI, Header

from knowwork_server.auth.principal import Principal
from knowwork_server.knowledge import build_router

from .knowflow_stub import SERVICE_KEY

pytestmark = pytest.mark.anyio
BASE = "/api/workspaces/ws1/knowledge-sources"


@pytest.fixture
async def client(env):
    async def current_principal(x_user: str = Header()) -> Principal:
        return Principal(user_id=x_user, auth_method="session")

    app = FastAPI()
    app.include_router(build_router(env.knowledge.admin, current_principal))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as http:
        yield http


def as_user(user):
    return {"x-user": user}


async def test_crud_flow_never_leaks_secrets(client, env):
    created = await client.post(BASE, headers=as_user("alice"), json={
        "name": "合同库", "base_url": "http://kf.local", "service_key": SERVICE_KEY,
        "config": {"auth_mode": "assertion", "dataset_ids": ["ds1"]}})
    assert created.status_code == 201, created.text
    body = created.json()
    assert SERVICE_KEY not in created.text and body["service_key"] == {"configured": True, "last4": SERVICE_KEY[-4:]}
    source_id = body["id"]
    listed = await client.get(BASE, headers=as_user("alice"))
    assert [s["id"] for s in listed.json()] == [source_id]
    patched = await client.patch(f"{BASE}/{source_id}", headers=as_user("bob"), json={"name": "新名字"})
    assert patched.json()["name"] == "新名字"
    tested = await client.post(f"{BASE}/{source_id}/test", headers=as_user("alice"))
    assert tested.json() == {"ok": True, "message": "连接正常"}
    erin = await client.post(f"{BASE}/{source_id}/test", headers=as_user("erin"))
    assert erin.status_code == 404
    assert (await client.delete(f"{BASE}/{source_id}", headers=as_user("alice"))).status_code == 204
    assert (await client.get(f"{BASE}/{source_id}", headers=as_user("alice"))).status_code == 404


async def test_knowledge_bases_endpoint_groups_bases_by_source(client):
    created = await client.post(BASE, headers=as_user("alice"), json={
        "name": "共享库", "base_url": "http://kf.local", "account_key": "ragflow-alicekey",
        "config": {"auth_mode": "shared", "dataset_ids": ["ds1", "ds2"]}})
    assert created.status_code == 201, created.text
    assert created.json()["account_key"]["configured"] and "ragflow-alicekey" not in created.text
    source_id = created.json()["id"]
    listed = await client.get("/api/workspaces/ws1/knowledge-bases", headers=as_user("bob"))
    assert listed.status_code == 200
    assert listed.json() == [{"source_id": source_id, "source_name": "共享库", "auth_mode": "shared", "enabled": True,
                              "error": None, "bases": [{"id": f"{source_id}.ds1", "kb_id": "ds1", "name": "库ds1"},
                                                       {"id": f"{source_id}.ds2", "kb_id": "ds2", "name": "库ds2"}]}]
    assert (await client.get("/api/workspaces/ws1/knowledge-bases", headers=as_user("carol"))).json() == []
    open_all = await client.post(BASE, headers=as_user("alice"), json={
        "name": "x", "base_url": "http://kf", "account_key": "ragflow-alicekey", "config": {"auth_mode": "shared"}})
    assert open_all.status_code == 201, "统一账号留空开放范围 = 那个账号读得到的全部"


async def test_validation_and_permissions(client):
    bad = await client.post(BASE, headers=as_user("alice"), json={"name": "x", "base_url": "javascript:alert(1)"})
    assert bad.status_code == 422
    short = await client.post(BASE, headers=as_user("alice"),
                              json={"name": "x", "base_url": "http://kf", "service_key": "short"})
    assert short.status_code == 422
    denied = await client.post(BASE, headers=as_user("carol"), json={
        "name": "x", "base_url": "http://kf", "service_key": SERVICE_KEY})
    assert denied.status_code == 403


async def test_personal_key_mode_is_gone(client, env):
    """2026-10-20 去掉个人 Key 方式：新建不接受，个人 Key 接口也没有了；有知识库授权的成员看到的是摘要。"""
    refused = await client.post(BASE, headers=as_user("alice"), json={
        "name": "个人库", "base_url": "http://kf.local", "config": {"auth_mode": "api_key"}})
    assert refused.status_code == 422
    created = await client.post(BASE, headers=as_user("alice"), json={
        "name": "共享库", "base_url": "http://kf.local", "account_key": "ragflow-alicekey"})
    assert created.status_code == 201 and created.json()["auth_mode"] == "shared", "默认就是统一账号"
    source_id = created.json()["id"]
    gone = await client.put(f"{BASE}/{source_id}/personal-key", headers=as_user("alice"), json={"api_key": "x" * 10})
    assert gone.status_code in (404, 405)
    await env.access.grant("alice", "ws1", "user:carol", f"knowledge_base:{source_id}.*", "use")
    view = await client.get(f"{BASE}/{source_id}", headers=as_user("carol"))
    assert view.json() == {"id": source_id, "name": "共享库", "kind": "knowflow", "enabled": True,
                           "auth_mode": "shared"}
