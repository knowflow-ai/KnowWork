"""扩展提供的跳转类登录（企业微信等）：按登录方式 id 登记 SsoProvider，走公共的 state、回调与认人流程。

在客户端里打开（浏览器标识命中 in_client_agents，或带 in_client=1）换成客户端里的免登授权，回调是同一个。
另有域名根目录的校验文件：扩展按文件名给内容，网关转到 /api/auth/verify-files/{name}。
"""

from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from knowwork_server.identity.errors import SsoFailure
from knowwork_server.identity.external import ExternalProfile
from knowwork_server.identity.sso import SsoProvider

from .conftest import BASE_URL, make_app

pytestmark = pytest.mark.anyio
LOGIN = "/api/auth/sso/ds_wecom/login"
CALLBACK = "/api/auth/sso/ds_wecom/callback"
REALM = "wecom:ww-corp"


class FakePlatform:
    """平台那边：记下每次发起时是不是在客户端里，授权码 good 换出张三。"""

    def __init__(self) -> None:
        self.started: list[bool] = []

    async def authorization_url(self, *, state: str, nonce: str, code_verifier: str, in_client: bool = False) -> str:
        self.started.append(in_client)
        host = "open.example.test" if in_client else "login.example.test"
        return f"https://{host}/authorize?state={state}"

    async def profile(self, *, code: str, code_verifier: str, nonce_hash: str) -> ExternalProfile:
        if code != "good":
            raise SsoFailure("授权码不对")
        return ExternalProfile(realm=REALM, subject="zhangsan", name="张三", account="zhangsan")


@pytest.fixture
def platform():
    return FakePlatform()


@pytest.fixture
async def browser(identity, admins, platform):
    async def resolve(method_id: str):
        if method_id != "ds_wecom":
            return None
        return SsoProvider(id="ds_wecom", kind="wecom", client=platform, allow_signup=True,
                           in_client_agents=("wxwork",))

    identity.redirect_logins.add(resolve)

    async def lookup(name: str):
        return "abcd1234" if name == "WW_verify_abcd1234.txt" else None

    identity.verify_files.add(lookup)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=make_app(identity, admins)),
                                 base_url=BASE_URL) as http:
        yield http


async def _state(started: httpx.Response) -> str:
    assert started.status_code == 302
    return parse_qs(urlparse(started.headers["location"]).query)["state"][0]


async def test_an_extension_login_goes_through_the_shared_callback(browser, platform):
    state = await _state(await browser.get(LOGIN))
    assert platform.started == [False]
    done = await browser.get(CALLBACK, params={"code": "good", "state": state})
    assert done.status_code == 302 and done.headers["location"] == "/"
    me = (await browser.get("/api/auth/me")).json()
    assert me["authMethod"] == "wecom" and me["name"] == "张三" and me["email"] is None


async def test_inside_the_client_it_starts_the_in_client_authorization(browser, platform):
    started = await browser.get(LOGIN, headers={"User-Agent": "Mozilla/5.0 wxwork/4.1.30 MicroMessenger/7.0.1"})
    assert urlparse(started.headers["location"]).hostname == "open.example.test"
    await browser.get(LOGIN, params={"in_client": "1"})
    await browser.get(LOGIN, headers={"User-Agent": "Mozilla/5.0 Chrome/130"})
    assert platform.started == [True, True, False]


async def test_a_wrong_code_or_state_fails_and_unknown_methods_are_404(browser):
    state = await _state(await browser.get(LOGIN))
    assert (await browser.get(CALLBACK, params={"code": "bad", "state": state})).status_code == 401
    state = await _state(await browser.get(LOGIN))
    assert (await browser.get(CALLBACK, params={"code": "good", "state": "forged"})).status_code == 401
    assert (await browser.get("/api/auth/sso/ds_other/login")).status_code == 404


async def test_verify_files_are_served_as_plain_text(browser):
    found = await browser.get("/api/auth/verify-files/WW_verify_abcd1234.txt")
    assert found.status_code == 200 and found.text == "abcd1234"
    assert found.headers["content-type"].startswith("text/plain")
    assert (await browser.get("/api/auth/verify-files/WW_verify_other.txt")).status_code == 404
    assert (await browser.get("/api/auth/verify-files/..%2Fsecrets.txt")).status_code == 404
    assert (await browser.get("/api/auth/verify-files/WW_verify_abcd1234.html")).status_code == 404
