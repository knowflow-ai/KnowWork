"""账号密码登录与浏览器会话：成功、失败统一提示、禁用、过期、吊销、篡改、滑动续期、限流。"""

from datetime import timedelta

import pytest
from sqlalchemy import select, update

from knowwork_server.identity import IdentityConfig, build_identity
from knowwork_server.identity.errors import EmailTaken, InvalidInput
from knowwork_server.identity.models import AuthSession, User

from .conftest import BASE_URL, PASSWORD, login

pytestmark = pytest.mark.anyio


async def test_login_sets_hardened_cookie_and_me_works(client, alice, sessionmaker):
    response = await login(client, email="  ALICE@example.com ")
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "samesite=lax" in cookie.lower()
    me = await client.get("/api/auth/me")
    assert me.json() == {"id": alice.id, "email": "alice@example.com", "name": "Alice",
                         "authMethod": "session", "scopes": None}
    token = client.cookies["kw_session"]
    async with sessionmaker() as session:
        stored = (await session.execute(select(AuthSession.token_hash))).scalars().all()
    assert token not in stored and len(stored) == 1


@pytest.mark.parametrize("email,password", [
    ("alice@example.com", "wrong password"),
    ("nobody@example.com", PASSWORD),
    ("not-an-email", PASSWORD),
])
async def test_login_failures_share_one_message(client, alice, email, password):
    response = await login(client, email=email, password=password)
    assert response.status_code == 401
    assert response.json()["detail"] == "邮箱或密码错误"
    assert "kw_session" not in client.cookies


async def test_me_requires_credentials(client):
    response = await client.get("/api/auth/me")
    assert response.status_code == 401
    assert response.json()["detail"] == "未登录或凭证无效"


async def test_tampered_cookie_is_rejected(client, alice):
    await login(client)
    token = client.cookies["kw_session"]
    client.cookies.set("kw_session", token[:-2] + ("aa" if not token.endswith("aa") else "bb"), domain="kw.test")
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_logout_revokes_session(client, alice):
    await login(client)
    token = client.cookies["kw_session"]
    assert (await client.post("/api/auth/logout")).status_code == 204
    client.cookies.set("kw_session", token, domain="kw.test")
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_logout_without_session_is_harmless(client):
    assert (await client.post("/api/auth/logout")).status_code == 204


async def test_disabled_user_cannot_log_in_and_sessions_die(client, identity, alice):
    await login(client)
    token = client.cookies["kw_session"]
    assert await identity.users.set_disabled(alice.id, True)
    client.cookies.set("kw_session", token, domain="kw.test")
    assert (await client.get("/api/auth/me")).status_code == 401
    assert (await login(client)).status_code == 401
    assert await identity.users.set_disabled("missing", True) is False


async def test_disabled_user_is_rejected_even_with_unrevoked_session(identity, sessionmaker, alice):
    """即使会话记录没被吊销（例如直接改库禁用），解析时也按用户状态拒绝。"""
    issued = await identity.sessions.create(alice.id)
    async with sessionmaker() as session, session.begin():
        await session.execute(update(User).where(User.id == alice.id).values(status="disabled"))
    assert await identity.sessions.resolve(issued.token) is None


async def test_session_expires(client, alice, clock):
    await login(client)
    clock.advance(days=7, seconds=1)
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_sliding_session_extends(sessionmaker, clock, alice):
    identity = build_identity(sessionmaker, IdentityConfig(public_base_url=BASE_URL, session_sliding=True,
                                                           session_ttl=timedelta(days=2)), clock=clock)
    issued = await identity.sessions.create(alice.id)
    clock.advance(days=1, hours=12)
    assert await identity.sessions.resolve(issued.token) is not None
    clock.advance(days=1, hours=12)
    principal = await identity.sessions.resolve(issued.token)
    assert principal is not None and principal.session_id == issued.session_id


async def test_revoke_all_and_bad_tokens(identity, alice):
    issued = await identity.sessions.create(alice.id)
    await identity.sessions.revoke_all(alice.id)
    assert await identity.sessions.resolve(issued.token) is None
    assert await identity.sessions.resolve("x" * 500) is None
    with pytest.raises(ValueError):
        await identity.sessions.create(alice.id, method="api_key")


async def test_login_is_throttled_per_account(client, alice):
    for _ in range(5):
        assert (await login(client, password="wrong password")).status_code == 401
    blocked = await login(client)
    assert blocked.status_code == 429


async def test_successful_login_resets_account_counter(client, alice):
    for _ in range(4):
        await login(client, password="wrong password")
    assert (await login(client)).status_code == 200
    for _ in range(4):
        assert (await login(client, password="wrong password")).status_code == 401
    assert (await login(client)).status_code == 200


async def test_password_rehash_and_policy(identity):
    with pytest.raises(InvalidInput):
        await identity.users.create_user(email="bob@example.com", name="Bob", password="short")
    with pytest.raises(InvalidInput):
        await identity.users.create_user(email="bad", name="Bob", password=PASSWORD)
    await identity.users.create_user(email="bob@example.com", name="Bob", password=PASSWORD)
    with pytest.raises(EmailTaken):
        await identity.users.create_user(email="BOB@example.com", name="Bob2", password=PASSWORD)
    assert (await identity.users.find_by_email("Bob@Example.com")).name == "Bob"
    assert await identity.users.get("missing") is None
