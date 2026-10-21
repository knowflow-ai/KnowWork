"""调用者判定：API Key 范围与绑定空间、建号后自动入默认空间、权限服务不可用时 503、OIDC 配置校验。"""

import secrets

import httpx
import pytest

from knowwork_server.access.members import DEFAULT_WORKSPACE_ID
from knowwork_server.access.vocab import WORKSPACE_RESOURCE, Action
from knowwork_server.app import build_runtime, create_public_app
from knowwork_server.config import Settings
from knowwork_server.db.migrate import upgrade_to_head

pytestmark = pytest.mark.anyio

PASSWORD = "correct horse battery"


@pytest.fixture
async def env(fresh_url):
    await upgrade_to_head(fresh_url)
    runtime = build_runtime(Settings(database_url=fresh_url, master_key=secrets.token_hex(32),
                                     host_token="h" * 32, cookie_secure=False))
    assert await runtime.access.reload()
    owner = await runtime.identity.users.create_user(email="owner@example.com", name="所有者", password=PASSWORD)
    await runtime.access.ensure_default_workspace(owner.id)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_public_app(runtime)), base_url="http://kw")
    try:
        yield runtime, owner, client
    finally:
        await client.aclose()
        await runtime.stop()


async def key(runtime, user_id, *scopes, workspace_id=None):
    issued = await runtime.identity.api_keys.create(user_id=user_id, name="t", scopes=list(scopes),
                                                    workspace_id=workspace_id)
    return {"authorization": f"Bearer {issued.plaintext}"}


async def test_workspace_management_requires_its_own_scope(env):
    runtime, owner, client = env
    chat_only = await key(runtime, owner.id, "conversations:use")
    manager = await key(runtime, owner.id, "workspace:manage")
    members = f"/api/workspaces/{DEFAULT_WORKSPACE_ID}/members"
    assert (await client.get(members, headers=chat_only)).status_code == 403
    assert (await client.post(members, json={"user_id": "someone", "role": "owner"},
                              headers=chat_only)).status_code == 403
    assert (await client.get(members, headers=manager)).status_code == 200
    models = f"/api/workspaces/{DEFAULT_WORKSPACE_ID}/model-providers"
    assert (await client.get(models, headers=chat_only)).status_code == 403


async def test_key_bound_to_another_workspace_is_refused(env):
    runtime, owner, client = env
    elsewhere = await key(runtime, owner.id, "conversations:use", "workspace:manage", workspace_id="other")
    assert (await client.post("/api/conversations", json={}, headers=elsewhere)).status_code == 403
    members = f"/api/workspaces/{DEFAULT_WORKSPACE_ID}/members"
    assert (await client.get(members, headers=elsewhere)).status_code == 403


async def test_cannot_bind_key_to_a_workspace_you_do_not_belong_to(env):
    _, _, client = env
    login = await client.post("/api/auth/login", json={"email": "owner@example.com", "password": PASSWORD})
    assert login.status_code == 200, login.text
    denied = await client.post("/api/auth/api-keys", json={"name": "k", "scopes": ["conversations:use"],
                                                           "workspaceId": "other"})
    assert denied.status_code == 403
    allowed = await client.post("/api/auth/api-keys", json={"name": "k", "scopes": ["conversations:use"],
                                                            "workspaceId": DEFAULT_WORKSPACE_ID})
    assert allowed.status_code == 201, allowed.text


async def test_invited_user_joins_default_workspace_once(env):
    runtime, owner, client = env
    invitation = await runtime.identity.invitations.create(email="new@example.com", invited_by=owner.id,
                                                          caller_is_admin=True)
    accepted = await client.post("/api/auth/invitations/accept",
                                 json={"token": invitation.token, "name": "新人", "password": PASSWORD})
    assert accepted.status_code == 201, accepted.text
    user_id = accepted.json()["id"]
    assert runtime.access.check(user_id, DEFAULT_WORKSPACE_ID, WORKSPACE_RESOURCE, Action.USE)
    assert not runtime.access.check(user_id, DEFAULT_WORKSPACE_ID, WORKSPACE_RESOURCE, Action.EDIT)
    roles = {m.user_id: m.role for m in await runtime.access.list_members(owner.id, DEFAULT_WORKSPACE_ID)}
    assert roles[user_id] == "viewer", "新注册用户默认是查看者"
    assert await runtime.access.join_default_workspace(user_id) is False, "已是成员时不重复加入"


async def test_join_before_default_workspace_exists_is_a_no_op(fresh_url):
    await upgrade_to_head(fresh_url)
    runtime = build_runtime(Settings(database_url=fresh_url, master_key=secrets.token_hex(32), host_token="h" * 32))
    try:
        assert await runtime.access.reload()
        user = await runtime.identity.users.create_user(email="a@example.com", name="a")
        assert await runtime.access.join_default_workspace(user.id) is False
    finally:
        await runtime.stop()


async def test_policy_engine_down_is_503_not_403(env):
    runtime, owner, client = env
    chat = await key(runtime, owner.id, "conversations:use")
    runtime.access.engine.invalidate()
    response = await client.post("/api/conversations", json={}, headers=chat)
    assert response.status_code == 503


def test_oidc_issuer_without_client_settings_refuses_to_start():
    settings = Settings(master_key="m" * 32, host_token="h" * 32, oidc_issuer="https://idp.example.com")
    with pytest.raises(ValueError, match="KNOWWORK_OIDC_CLIENT_ID"):
        build_runtime(settings)
