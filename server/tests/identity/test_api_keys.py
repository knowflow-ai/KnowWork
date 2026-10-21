"""API Key（K7）：格式、只存哈希、明文只给一次、scope、过期、吊销、最后使用时间、禁用用户。"""

import re
from datetime import timedelta

import pytest
from sqlalchemy import select

from knowwork_server.auth.principal import Principal
from knowwork_server.identity.errors import InvalidInput
from knowwork_server.identity.models import ApiKey

from .conftest import login

pytestmark = pytest.mark.anyio


async def _create(client, scopes=("conversations:write",), **extra):
    return await client.post("/api/auth/api-keys", json={"name": "ci", "scopes": list(scopes), **extra})


def _bearer(key: str) -> dict:
    return {"authorization": f"Bearer {key}"}


@pytest.fixture
async def logged_in(client, alice):
    await login(client)
    return client


async def test_create_list_and_use(logged_in, sessionmaker):
    created = await _create(logged_in, workspaceId="ws-1")
    assert created.status_code == 201
    key = created.json()["key"]
    assert re.fullmatch(r"ak_[a-z0-9]{8}_[A-Za-z0-9_-]{43}", key)
    assert key.split("_")[1] == created.json()["prefix"]
    async with sessionmaker() as session:
        row = (await session.execute(select(ApiKey))).scalar_one()
    assert key not in (row.secret_hash, row.prefix) and row.workspace_id == "ws-1"

    listed = (await logged_in.get("/api/auth/api-keys")).json()
    assert len(listed) == 1 and "key" not in listed[0] and listed[0]["lastUsedAt"] is None

    logged_in.cookies.clear()
    probe = await logged_in.post("/api/probe/write", headers=_bearer(key))
    assert probe.status_code == 200 and probe.json()["method"] == "api_key"
    me = (await logged_in.get("/api/auth/me", headers=_bearer(key))).json()
    assert me["authMethod"] == "api_key" and me["scopes"] == ["conversations:write"]


async def test_wrong_scope_is_forbidden(logged_in):
    key = (await _create(logged_in, scopes=("conversations:read",))).json()["key"]
    logged_in.cookies.clear()
    response = await logged_in.post("/api/probe/write", headers=_bearer(key))
    assert response.status_code == 403


async def test_session_principal_is_not_scope_limited(logged_in):
    assert (await logged_in.post("/api/probe/write")).status_code == 200


@pytest.mark.parametrize("header", [
    "Bearer ak_abcdefgh_" + "x" * 43,
    "Bearer ak_short_x",
    "Bearer something-else",
    "Basic ak_abcdefgh_" + "x" * 43,
])
async def test_unknown_or_malformed_keys_are_unauthorized(client, header):
    response = await client.post("/api/probe/write", headers={"authorization": header})
    assert response.status_code == 401 and response.json()["detail"] == "未登录或凭证无效"


async def test_tampered_secret_with_valid_prefix(logged_in):
    key = (await _create(logged_in)).json()["key"]
    logged_in.cookies.clear()
    tampered = key[:-1] + ("A" if key[-1] != "A" else "B")
    assert (await logged_in.post("/api/probe/write", headers=_bearer(tampered))).status_code == 401


async def test_revoked_key_is_rejected(logged_in):
    created = (await _create(logged_in)).json()
    assert (await logged_in.delete(f"/api/auth/api-keys/{created['id']}")).status_code == 204
    assert (await logged_in.delete(f"/api/auth/api-keys/{created['id']}")).status_code == 404
    logged_in.cookies.clear()
    assert (await logged_in.post("/api/probe/write", headers=_bearer(created["key"]))).status_code == 401


async def test_expired_key_is_rejected(logged_in, clock):
    expires = (clock.now + timedelta(hours=1)).isoformat()
    key = (await _create(logged_in, expiresAt=expires)).json()["key"]
    logged_in.cookies.clear()
    assert (await logged_in.post("/api/probe/write", headers=_bearer(key))).status_code == 200
    clock.advance(hours=1)
    assert (await logged_in.post("/api/probe/write", headers=_bearer(key))).status_code == 401


async def test_disabled_owner_key_is_rejected(logged_in, identity, alice):
    key = (await _create(logged_in)).json()["key"]
    await identity.users.set_disabled(alice.id, True)
    logged_in.cookies.clear()
    assert (await logged_in.post("/api/probe/write", headers=_bearer(key))).status_code == 401


async def test_last_used_is_recorded_and_throttled(identity, alice, clock):
    issued = await identity.api_keys.create(user_id=alice.id, name="x", scopes=["a:b"])
    assert await identity.api_keys.resolve(issued.plaintext) is not None
    first = (await identity.api_keys.list(alice.id))[0].last_used_at
    assert first == clock.now
    clock.advance(seconds=10)
    await identity.api_keys.resolve(issued.plaintext)
    assert (await identity.api_keys.list(alice.id))[0].last_used_at == first
    clock.advance(seconds=60)
    await identity.api_keys.resolve(issued.plaintext)
    assert (await identity.api_keys.list(alice.id))[0].last_used_at == clock.now


async def test_api_key_cannot_manage_keys(logged_in):
    key = (await _create(logged_in)).json()["key"]
    logged_in.cookies.clear()
    assert (await logged_in.get("/api/auth/api-keys", headers=_bearer(key))).status_code == 403
    assert (await logged_in.post("/api/auth/api-keys", headers=_bearer(key),
                                 json={"name": "x", "scopes": ["a:b"]})).status_code == 403


@pytest.mark.parametrize("payload", [
    {"name": "x", "scopes": ["Bad Scope"]},
    {"name": "x", "scopes": []},
    {"name": "x", "scopes": ["a:b"], "expiresAt": "2020-01-01T00:00:00+00:00"},
    {"name": "x", "scopes": ["a:b"], "expiresAt": "2030-01-01T00:00:00"},
    {"name": "x", "scopes": ["a:b"], "extra": 1},
])
async def test_create_validates_input(logged_in, payload):
    assert (await logged_in.post("/api/auth/api-keys", json=payload)).status_code == 422


async def test_cannot_revoke_someone_elses_key(identity, logged_in):
    bob = await identity.users.create_user(email="bob@example.com", name="Bob", password="correct horse battery")
    issued = await identity.api_keys.create(user_id=bob.id, name="x", scopes=["a:b"])
    assert (await logged_in.delete(f"/api/auth/api-keys/{issued.info.id}")).status_code == 404
    assert await identity.api_keys.resolve(issued.plaintext) is not None


async def test_service_validation(identity, alice):
    with pytest.raises(InvalidInput):
        await identity.api_keys.create(user_id=alice.id, name="x", scopes=[])


def test_principal_scopes():
    key = Principal(user_id="u", auth_method="api_key", api_key_id="k", scopes=frozenset({"a:read", "a:write"}))
    assert key.allows("a:read") and key.allows("a:read", "a:write") and not key.allows("b:read")
    assert key.is_api_key
    session = Principal(user_id="u", auth_method="session", session_id="s")
    assert session.allows("anything:write") and not session.is_api_key
    with pytest.raises(AttributeError):
        session.user_id = "other"
