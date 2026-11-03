"""后台配置的登录方式：OIDC 与通用 OAuth2 的增删改（只给系统管理员、密钥不出库），以及用它们走完整个登录。"""

from typing import Annotated
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import Depends, HTTPException

from knowwork_server.auth.principal import Principal
from knowwork_server.identity import IdentityConfig, OidcConfig, build_identity
from knowwork_server.identity.providers_api import build_router as providers_router
from knowwork_server.knowledge.crypto import SecretBox

from . import fake_idp
from .conftest import BASE_URL, PASSWORD, login, make_app
from .fake_oauth2 import AUTHORIZE_URL, CLIENT_ID, CLIENT_SECRET, TOKEN_URL, USERINFO_URL, FakeOAuth2

pytestmark = pytest.mark.anyio
API = "/api/system/login-providers"


def _oauth2_body(**config) -> dict:
    return {"kind": "oauth2", "name": "统一认证", "client_secret": CLIENT_SECRET, "config": {
        "authorize_url": AUTHORIZE_URL, "token_url": TOKEN_URL, "userinfo_url": USERINFO_URL,
        "client_id": CLIENT_ID, "scopes": ["profile"], "subject_path": "data.user.id",
        "name_path": "data.user.name", "email_path": "data.user.mail", **config}}


def _oidc_body() -> dict:
    return {"kind": "oidc", "name": "身份中台", "client_secret": fake_idp.CLIENT_SECRET,
            "config": {"issuer": fake_idp.ISSUER, "client_id": fake_idp.CLIENT_ID}}


def _by_host(apps: dict):
    """把两个假服务端按主机名挂到同一个 HTTP 客户端上（业务服务只用一个客户端出去）。"""
    async def app(scope, receive, send):
        host = dict(scope["headers"]).get(b"host", b"").decode()
        await apps[host](scope, receive, send)
    return app


@pytest.fixture
def corp():
    return FakeOAuth2()


@pytest.fixture
def idp():
    return fake_idp.FakeIdp()


@pytest.fixture
async def outbound(corp, idp):
    apps = {urlparse(AUTHORIZE_URL).netloc: corp.app(), urlparse(fake_idp.ISSUER).netloc: idp.app()}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_by_host(apps))) as http:
        yield http


@pytest.fixture
def env_oidc():
    return None


@pytest.fixture
def identity(sessionmaker, clock, outbound, env_oidc):
    config = IdentityConfig(public_base_url=BASE_URL, oidc=env_oidc)
    return build_identity(sessionmaker, config, clock=clock, sso_http=outbound, secrets=SecretBox(b"k" * 32))


@pytest.fixture
def app(identity, admins):
    app = make_app(identity, admins)

    async def system_admin(who: Annotated[Principal, Depends(identity.get_principal)]) -> Principal:
        if who.user_id not in admins:
            raise HTTPException(status_code=403, detail="无权限执行该操作")
        return who

    app.include_router(providers_router(identity.providers, system_admin))
    return app


@pytest.fixture
async def admin_client(app, identity, admins):
    boss = await identity.users.create_user(email="boss@example.com", name="Boss", password=PASSWORD)
    admins.add(boss.id)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL) as http:
        assert (await login(http, email="boss@example.com")).status_code == 200
        yield http


@pytest.fixture
async def browser(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL) as http:
        yield http


async def _create(admin_client, body) -> dict:
    response = await admin_client.post(API, json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def _sso(browser, outbound, provider_id: str, **tamper) -> httpx.Response:
    """走一遍跳转登录：入口 → 对方同意 → 回调。"""
    start = await browser.get(f"/api/auth/sso/{provider_id}/login")
    assert start.status_code == 302, start.text
    consent = await outbound.get(start.headers["location"])
    callback = urlparse(consent.headers["location"])
    params = {key: values[0] for key, values in parse_qs(callback.query).items()}
    return await browser.get(callback.path, params={**params, **tamper})


async def test_admin_creates_a_provider_and_the_secret_never_leaves(admin_client):
    created = await _create(admin_client, _oauth2_body())
    assert created["kind"] == "oauth2" and created["enabled"] is True and created["signup"] == "known_only"
    assert created["has_secret"] is True and created["secret_last4"] == CLIENT_SECRET[-4:]
    assert created["callback_url"] == f"{BASE_URL}/api/auth/sso/{created['id']}/callback"
    listed = await admin_client.get(API)
    assert [p["id"] for p in listed.json()["providers"]] == [created["id"]]
    assert CLIENT_SECRET not in listed.text and CLIENT_SECRET not in str(created)


async def test_only_the_system_admin_manages_providers(browser, identity, admin_client):
    await identity.users.create_user(email="alice@example.com", name="Alice", password=PASSWORD)
    await login(browser)
    assert (await browser.get(API)).status_code == 403
    assert (await browser.post(API, json=_oauth2_body())).status_code == 403


@pytest.mark.parametrize("body", [
    _oauth2_body(token_url="http://sso.corp.test/oauth/token"),            # 不是 https
    _oauth2_body(userinfo_url="https://user:pw@sso.corp.test/api/me"),     # 地址里带凭证
    _oauth2_body(subject_path="data..id"),                                 # 路径写法不对
    _oauth2_body(token_auth="jwt"),
    _oauth2_body(surprise=True),                                           # 不认识的字段
    {**_oidc_body(), "config": {**_oidc_body()["config"], "algorithms": ["HS256"]}},
    {**_oidc_body(), "config": {**_oidc_body()["config"], "issuer": "http://idp.test"}},
    {**_oauth2_body(), "kind": "saml"},
    {**_oauth2_body(), "client_secret": ""},
])
async def test_invalid_settings_are_rejected(admin_client, body):
    assert (await admin_client.post(API, json=body)).status_code == 422


async def test_local_http_is_allowed_for_trying_things_out(admin_client):
    body = _oauth2_body(authorize_url="http://127.0.0.1:9000/authorize", token_url="http://localhost:9000/token")
    assert (await admin_client.post(API, json=body)).status_code == 201


async def test_update_keeps_the_secret_unless_a_new_one_is_given(admin_client, identity):
    created = await _create(admin_client, _oauth2_body())
    url = f"{API}/{created['id']}"
    renamed = (await admin_client.patch(url, json={"name": "集团统一认证", "enabled": False})).json()
    assert renamed["name"] == "集团统一认证" and renamed["secret_last4"] == CLIENT_SECRET[-4:]
    assert [m.id for m in await identity.methods.list()] == []  # 停用的不出现在登录页
    changed = (await admin_client.patch(url, json={"client_secret": "another-secret-1234"})).json()
    assert changed["secret_last4"] == "1234"
    assert (await admin_client.patch(url, json={"config": {"issuer": "x"}})).status_code == 422
    assert (await admin_client.patch(f"{API}/lp_missing", json={"name": "x"})).status_code == 404


async def test_moving_the_token_url_needs_the_secret_again(admin_client):
    """改了令牌地址又不重填密钥：拒绝，免得把库里存的客户端密钥发到别的地址。"""
    created = await _create(admin_client, _oauth2_body())
    url = f"{API}/{created['id']}"
    config = _oauth2_body()["config"]
    moved = {**config, "token_url": "https://evil.example/oauth/token"}
    refused = await admin_client.patch(url, json={"config": moved})
    assert refused.status_code == 422 and "重新填写客户端密钥" in refused.json()["detail"]
    assert (await admin_client.patch(url, json={"config": moved, "client_secret": "another-secret-1234"})) \
        .status_code == 200
    # 只改别的（用户信息地址、字段路径）照常保存
    other = {**moved, "userinfo_url": "https://sso.corp.test/api/v2/me", "name_path": "data.name"}
    assert (await admin_client.patch(url, json={"config": other})).status_code == 200


async def test_delete_provider(admin_client, browser):
    created = await _create(admin_client, _oauth2_body())
    assert (await admin_client.delete(f"{API}/{created['id']}")).status_code == 204
    assert (await admin_client.get(API)).json()["providers"] == []
    assert (await browser.get(f"/api/auth/sso/{created['id']}/login")).status_code == 404


async def test_login_methods_list_enabled_providers(admin_client, browser):
    created = await _create(admin_client, _oauth2_body())
    methods = (await browser.get("/api/auth/methods")).json()["methods"]
    assert methods == [{"id": created["id"], "kind": "oauth2", "label": "统一认证", "style": "redirect",
                        "url": f"/api/auth/sso/{created['id']}/login", "inClientAgents": []}]


async def test_oauth2_login_maps_nested_fields_and_trusted_email_links_the_account(admin_client, browser, outbound,
                                                                                  identity, corp):
    alice = await identity.users.create_user(email="zhangsan@corp.test", name="张三", password=PASSWORD)
    created = await _create(admin_client, _oauth2_body(email_trusted=True))
    response = await _sso(browser, outbound, created["id"])
    assert response.status_code == 302 and response.headers["location"] == "/"
    me = (await browser.get("/api/auth/me")).json()
    assert me["id"] == alice.id and me["authMethod"] == "oauth2"
    assert corp.seen_scopes == ["profile"]
    # 绑上以后对方改了邮箱，仍按外部 ID 认出同一个人
    corp.user = {**corp.user, "mail": "renamed@corp.test"}
    browser.cookies.clear()
    assert (await _sso(browser, outbound, created["id"])).status_code == 302
    assert (await browser.get("/api/auth/me")).json()["id"] == alice.id


async def test_untrusted_email_does_not_link_and_unknown_people_are_refused(admin_client, browser, outbound,
                                                                         identity):
    await identity.users.create_user(email="zhangsan@corp.test", name="张三", password=PASSWORD)
    created = await _create(admin_client, _oauth2_body())  # 邮箱默认不可信
    assert (await _sso(browser, outbound, created["id"])).status_code == 401


async def test_auto_signup_creates_the_person_without_the_untrusted_email(admin_client, browser, outbound):
    created = await _create(admin_client, {**_oauth2_body(), "signup": "auto"})
    assert (await _sso(browser, outbound, created["id"])).status_code == 302
    me = (await browser.get("/api/auth/me")).json()
    assert me["email"] is None and me["name"] == "张三"


async def test_post_client_auth_query_token_and_form_encoded_token_response(admin_client, browser, outbound, corp):
    corp.token_auth, corp.userinfo_token, corp.token_as_form = "post", "query", True
    created = await _create(admin_client, {**_oauth2_body(token_auth="post", userinfo_token="query"),
                                           "signup": "auto"})
    assert (await _sso(browser, outbound, created["id"])).status_code == 302


async def test_without_pkce_when_the_server_does_not_support_it(admin_client, browser, outbound, corp):
    corp.require_pkce = False
    created = await _create(admin_client, {**_oauth2_body(pkce=False), "signup": "auto"})
    assert (await _sso(browser, outbound, created["id"])).status_code == 302


@pytest.mark.parametrize("breakage", ["token_error", "no_subject", "wrong_secret", "state", "provider_error"])
async def test_failures_end_in_the_same_message(admin_client, browser, outbound, corp, breakage):
    body = {**_oauth2_body(), "signup": "auto"}
    if breakage == "wrong_secret":
        body["client_secret"] = "not-the-secret"
    created = await _create(admin_client, body)
    if breakage == "token_error":
        corp.token_status = 500
    if breakage == "no_subject":
        corp.user = {"name": "没有 ID 的人"}
    tamper = {"state": "forged"} if breakage == "state" else {"error": "access_denied"} \
        if breakage == "provider_error" else {}
    response = await _sso(browser, outbound, created["id"], **tamper)
    assert response.status_code == 401 and response.json()["detail"] == "单点登录失败"


async def test_unknown_or_disabled_provider_has_no_login(admin_client, browser):
    created = await _create(admin_client, {**_oauth2_body(), "enabled": False})
    assert (await browser.get(f"/api/auth/sso/{created['id']}/login")).status_code == 404
    assert (await browser.get("/api/auth/sso/lp_missing/login")).status_code == 404


async def test_oidc_configured_in_the_admin_logs_in(admin_client, browser, outbound, identity):
    alice = await identity.users.create_user(email="alice@example.com", name="Alice", password=PASSWORD)
    created = await _create(admin_client, _oidc_body())
    assert created["callback_url"] == f"{BASE_URL}/api/auth/sso/{created['id']}/callback"
    assert (await _sso(browser, outbound, created["id"])).status_code == 302
    me = (await browser.get("/api/auth/me")).json()
    assert me["id"] == alice.id and me["authMethod"] == "oidc"


@pytest.mark.parametrize("env_oidc", [OidcConfig(issuer=fake_idp.ISSUER, client_id=fake_idp.CLIENT_ID,
                                                 client_secret=fake_idp.CLIENT_SECRET,
                                                 redirect_uri=fake_idp.REDIRECT_URI)])
async def test_environment_oidc_is_listed_read_only(admin_client):
    listed = (await admin_client.get(API)).json()["providers"]
    assert listed == [{"id": "oidc", "kind": "oidc", "name": "企业账号", "enabled": True, "signup": "known_only",
                       "config": {"issuer": fake_idp.ISSUER, "client_id": fake_idp.CLIENT_ID},
                       "has_secret": True, "secret_last4": fake_idp.CLIENT_SECRET[-4:], "callback_url": fake_idp.REDIRECT_URI,
                       "readonly": True, "created_at": None, "updated_at": None}]
    assert (await admin_client.patch(f"{API}/oidc", json={"name": "x"})).status_code == 409
    assert (await admin_client.delete(f"{API}/oidc")).status_code == 409
