"""OIDC 授权码 + PKCE：正常登录、按 email 匹配与绑定、只允许已知用户、各类篡改与过期。"""

import time
from dataclasses import replace
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from knowwork_server.identity import IdentityConfig, OidcConfig, build_identity

from .conftest import BASE_URL, make_app
from .fake_idp import CLIENT_ID, CLIENT_SECRET, ISSUER, REDIRECT_URI, FakeIdp

pytestmark = pytest.mark.anyio
CALLBACK = "/api/auth/oidc/callback"


@pytest.fixture
def idp():
    return FakeIdp()


@pytest.fixture
def oidc_config():
    return OidcConfig(issuer=ISSUER, client_id=CLIENT_ID, client_secret=CLIENT_SECRET, redirect_uri=REDIRECT_URI)


@pytest.fixture
async def idp_http(idp):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=idp.app()), base_url=ISSUER) as http:
        yield http


@pytest.fixture
def identity(sessionmaker, clock, oidc_config, idp_http):
    config = IdentityConfig(public_base_url=BASE_URL, oidc=oidc_config)
    return build_identity(sessionmaker, config, clock=clock, oidc_http=idp_http)


async def _authorize(client, idp_http) -> dict:
    """走到提供方回跳为止，返回回调的查询参数。"""
    start = await client.get("/api/auth/oidc/login")
    assert start.status_code == 302
    consent = await idp_http.get(start.headers["location"])
    query = parse_qs(urlparse(consent.headers["location"]).query)
    return {key: values[0] for key, values in query.items()}


async def _sso(client, idp_http, **tamper) -> httpx.Response:
    params = {**await _authorize(client, idp_http), **tamper}
    return await client.get(CALLBACK, params=params)


async def test_existing_user_logs_in_and_is_linked(client, idp_http, idp, alice):
    response = await _sso(client, idp_http)
    assert response.status_code == 302 and response.headers["location"] == "/"
    me = (await client.get("/api/auth/me")).json()
    assert me["id"] == alice.id and me["authMethod"] == "oidc"
    # 绑定后即使提供方那边的 email 变了，仍按 issuer + subject 认出同一个人
    idp.email = "renamed@example.com"
    client.cookies.clear()
    assert (await _sso(client, idp_http)).status_code == 302
    assert (await client.get("/api/auth/me")).json()["id"] == alice.id


async def test_unknown_user_rejected_when_only_known_allowed(client, idp_http, idp):
    idp.email = "stranger@example.com"
    response = await _sso(client, idp_http)
    assert response.status_code == 401 and response.json()["detail"] == "单点登录失败"


async def test_invited_user_is_created(client, idp_http, idp, identity):
    await identity.invitations.create(email="newbie@example.com", invited_by="root", caller_is_admin=True)
    idp.email = "NewBie@example.com"
    assert (await _sso(client, idp_http)).status_code == 302
    me = (await client.get("/api/auth/me")).json()
    assert me["email"] == "newbie@example.com" and me["name"] == "Alice From IdP"


async def test_open_registration_creates_user(sessionmaker, clock, oidc_config, idp_http, idp, admins):
    config = IdentityConfig(public_base_url=BASE_URL, oidc=replace(oidc_config, allow_only_known_users=False))
    identity = build_identity(sessionmaker, config, clock=clock, oidc_http=idp_http)
    idp.email = "open@example.com"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=make_app(identity, admins)),
                                 base_url=BASE_URL) as client:
        assert (await _sso(client, idp_http)).status_code == 302
        assert (await client.get("/api/auth/me")).json()["email"] == "open@example.com"


async def test_state_mismatch_is_rejected(client, idp_http, alice):
    assert (await _sso(client, idp_http, state="forged-state")).status_code == 401


async def test_missing_state_cookie_is_rejected(client, idp_http, alice):
    params = await _authorize(client, idp_http)
    client.cookies.clear()
    assert (await client.get(CALLBACK, params=params)).status_code == 401


async def test_callback_cannot_be_replayed(client, idp_http, alice):
    params = await _authorize(client, idp_http)
    cookie = client.cookies["kw_oidc"]
    assert (await client.get(CALLBACK, params=params)).status_code == 302
    client.cookies.set("kw_oidc", cookie, domain="kw.test", path=CALLBACK)
    assert (await client.get(CALLBACK, params=params)).status_code == 401


async def test_expired_login_state_is_rejected(client, idp_http, clock, alice):
    params = await _authorize(client, idp_http)
    clock.advance(minutes=11)
    assert (await client.get(CALLBACK, params=params)).status_code == 401


async def test_tampered_verifier_in_cookie_is_rejected(client, idp_http, alice):
    params = await _authorize(client, idp_http)
    state, _ = client.cookies["kw_oidc"].split(".")
    client.cookies.set("kw_oidc", f"{state}.forged-verifier", domain="kw.test", path=CALLBACK)
    assert (await client.get(CALLBACK, params=params)).status_code == 401


@pytest.mark.parametrize("overrides", [
    {"nonce": "attacker-nonce"},
    {"aud": "someone-else"},
    {"aud": [CLIENT_ID, "other"]},
    {"iss": "https://evil.test"},
    {"exp": int(time.time()) - 3600},
    {"email_verified": False},
])
async def test_bad_id_token_claims_are_rejected(client, idp_http, idp, alice, overrides):
    idp.overrides = overrides
    assert (await _sso(client, idp_http)).status_code == 401


async def test_symmetric_algorithm_is_rejected(client, idp_http, idp, alice):
    idp.sign_with = "HS256"
    assert (await _sso(client, idp_http)).status_code == 401


async def test_token_signed_by_unknown_key_is_rejected(client, idp_http, idp, alice):
    idp.sign_with = "rogue"  # 同 kid、同算法，但不是 JWKS 里公布的那把私钥
    assert (await _sso(client, idp_http)).status_code == 401


async def test_disabled_user_cannot_sso(client, idp_http, identity, alice):
    await identity.users.set_disabled(alice.id, True)
    assert (await _sso(client, idp_http)).status_code == 401


async def test_provider_error_and_missing_code(client, idp_http, alice):
    assert (await client.get(CALLBACK, params={"error": "access_denied"})).status_code == 401
    assert (await _sso(client, idp_http, code="")).status_code == 401


async def test_oidc_disabled_returns_404(sessionmaker, clock, admins):
    identity = build_identity(sessionmaker, IdentityConfig(public_base_url=BASE_URL), clock=clock)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=make_app(identity, admins)),
                                 base_url=BASE_URL) as client:
        assert (await client.get("/api/auth/oidc/login")).status_code == 404


async def test_provider_unavailable(sessionmaker, clock, oidc_config, admins):
    def broken(request):
        return httpx.Response(500)

    http = httpx.AsyncClient(transport=httpx.MockTransport(broken))
    identity = build_identity(sessionmaker, IdentityConfig(public_base_url=BASE_URL, oidc=oidc_config),
                              clock=clock, oidc_http=http)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=make_app(identity, admins)),
                                 base_url=BASE_URL) as client:
        assert (await client.get("/api/auth/oidc/login")).status_code == 502
    await http.aclose()


@pytest.mark.parametrize("algorithms", [(), ("none",), ("HS256",)])
def test_config_rejects_unsafe_algorithms(algorithms):
    with pytest.raises(ValueError):
        OidcConfig(issuer=ISSUER, client_id="c", client_secret="s", redirect_uri=REDIRECT_URI, algorithms=algorithms)
