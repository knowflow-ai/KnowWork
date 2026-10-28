"""调用者判定：API Key 范围与绑定空间、建号后自动入默认空间、权限服务不可用时 503、OIDC 配置校验。"""

import secrets

import httpx
import pytest

from knowwork_server.access.members import DEFAULT_WORKSPACE_ID
from knowwork_server.app import build_runtime, create_public_app
from knowwork_server.config import Settings
from knowwork_server.db.migrate import upgrade_to_head
from knowwork_server.identity.hooks import Placement
from knowwork_server.principals import MODELS_SCOPE, NOT_A_MEMBER, UNBOUND_KEY, place_new_user

CONV = "/api/workspaces/default/conversations"

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
    models = "/api/system/model-providers"
    assert (await client.get(models, headers=chat_only)).status_code == 403
    model_key = await key(runtime, owner.id, MODELS_SCOPE)
    assert (await client.get(models, headers=model_key)).status_code == 403, "全平台共用的配置不放 API Key"


async def test_key_bound_to_another_workspace_is_refused(env):
    runtime, owner, client = env
    elsewhere = await key(runtime, owner.id, "conversations:use", "workspace:manage", workspace_id="other")
    assert (await client.post(CONV, json={}, headers=elsewhere)).status_code == 403
    members = f"/api/workspaces/{DEFAULT_WORKSPACE_ID}/members"
    assert (await client.get(members, headers=elsewhere)).status_code == 403


async def test_conversations_follow_the_workspace_in_the_path(env):
    runtime, owner, client = env
    await runtime.access.create_workspace(workspace_id="ws2", name="二", owner_user_id="someone")
    chat = await key(runtime, owner.id, "conversations:use")
    assert (await client.get(CONV, headers=chat)).status_code == 200
    other = await client.get("/api/workspaces/ws2/conversations", headers=chat)
    assert other.status_code == 403 and other.json()["detail"] == NOT_A_MEMBER, "不是那个空间的成员"
    assert (await client.get("/api/workspaces/bad%20id/conversations", headers=chat)).status_code == 404


async def test_external_retrieval_needs_a_key_bound_to_a_workspace(env):
    runtime, owner, client = env
    unbound = await key(runtime, owner.id, "knowledge:read")
    refused = await client.get("/api/v1/knowledge-bases", headers=unbound)
    assert refused.status_code == 403 and refused.json()["detail"] == UNBOUND_KEY
    bound = await key(runtime, owner.id, "knowledge:read", workspace_id=DEFAULT_WORKSPACE_ID)
    assert (await client.get("/api/v1/knowledge-bases", headers=bound)).status_code == 200


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


async def test_invited_user_joins_the_inviting_workspace_once(env):
    runtime, owner, client = env
    invitation = await runtime.identity.invitations.create(
        email="new@example.com", invited_by=owner.id, placement=Placement(DEFAULT_WORKSPACE_ID, "admin"),
        caller_is_admin=True)
    accepted = await client.post("/api/auth/invitations/accept",
                                 json={"token": invitation.token, "name": "新人", "password": PASSWORD})
    assert accepted.status_code == 201, accepted.text
    user_id = accepted.json()["id"]
    roles = {m.user_id: m.role for m in await runtime.access.list_members(owner.id, DEFAULT_WORKSPACE_ID)}
    assert roles[user_id] == "admin", "按邀请定的角色加入"
    assert await runtime.access.join_workspace(user_id, DEFAULT_WORKSPACE_ID) is False, "已是成员时不重复加入"


async def test_uninvited_new_account_joins_the_default_workspace_as_member(env):
    runtime, owner, _ = env
    user = await runtime.identity.users.create_user(email="sso@example.com", name="单点")
    await place_new_user(runtime.access)(user.id, None)
    roles = {m.user_id: m.role for m in await runtime.access.list_members(owner.id, DEFAULT_WORKSPACE_ID)}
    assert roles[user.id] == "editor"


async def test_join_before_default_workspace_exists_is_a_no_op(fresh_url):
    await upgrade_to_head(fresh_url)
    runtime = build_runtime(Settings(database_url=fresh_url, master_key=secrets.token_hex(32), host_token="h" * 32))
    try:
        assert await runtime.access.reload()
        user = await runtime.identity.users.create_user(email="a@example.com", name="a")
        assert await runtime.access.join_workspace(user.id, DEFAULT_WORKSPACE_ID) is False
    finally:
        await runtime.stop()


async def test_policy_engine_down_is_503_not_403(env):
    runtime, owner, client = env
    chat = await key(runtime, owner.id, "conversations:use")
    runtime.access.engine.invalidate()
    response = await client.post(CONV, json={}, headers=chat)
    assert response.status_code == 503


def test_oidc_issuer_without_client_settings_refuses_to_start():
    settings = Settings(master_key="m" * 32, host_token="h" * 32, oidc_issuer="https://idp.example.com")
    with pytest.raises(ValueError, match="KNOWWORK_OIDC_CLIENT_ID"):
        build_runtime(settings)
