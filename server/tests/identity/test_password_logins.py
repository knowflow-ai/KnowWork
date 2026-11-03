"""账号密码类的外部登录（域账号等）：扩展登记一个按 id 取的提供方，登录页按登录方式列表显示。

失败一律「账号或密码错误」，按（登录方式 + 账号）与 IP 限流，同邮箱密码登录。认人走 external.py。
"""

import httpx
import pytest

from knowwork_server.identity.errors import SsoFailure
from knowwork_server.identity.external import ExternalProfile
from knowwork_server.identity.methods import LoginMethod
from knowwork_server.identity.password_logins import PasswordProvider

from .conftest import BASE_URL, make_app

pytestmark = pytest.mark.anyio
URL = "/api/auth/password/ds_ad"
REALM = "ldap:ds_ad"


class FakeDirectory:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def verify(self, username: str, password: str) -> ExternalProfile:
        self.calls.append((username, password))
        if (username, password) != ("zhangsan", "Corp-Pass-1"):
            raise SsoFailure("账号或密码不对")
        return ExternalProfile(realm=REALM, subject="guid-zhang", name="张三", account="zhangsan")


@pytest.fixture
def directory():
    return FakeDirectory()


@pytest.fixture
async def client(identity, admins, directory):
    async def resolve(method_id: str):
        if method_id != "ds_ad":
            return None
        return PasswordProvider(id="ds_ad", kind="ldap", verify=directory.verify)

    identity.password_logins.add(resolve)
    identity.methods.add(lambda: [LoginMethod(id="ds_ad", kind="ldap", label="集团域账号", style="password",
                                              url=URL)])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=make_app(identity, admins)),
                                 base_url=BASE_URL) as http:
        yield http


async def test_a_directory_login_creates_the_session(client):
    response = await client.post(URL, json={"username": "zhangsan", "password": "Corp-Pass-1"})
    assert response.status_code == 200 and response.json()["name"] == "张三"
    me = (await client.get("/api/auth/me")).json()
    assert me["authMethod"] == "ldap" and me["email"] is None
    # 第二次登录认出同一个人
    client.cookies.clear()
    again = await client.post(URL, json={"username": "zhangsan", "password": "Corp-Pass-1"})
    assert again.json()["id"] == me["id"]


async def test_failures_say_the_same_thing(client):
    wrong = await client.post(URL, json={"username": "zhangsan", "password": "nope"})
    assert wrong.status_code == 401 and wrong.json()["detail"] == "账号或密码错误"
    assert (await client.post("/api/auth/password/ds_other", json={"username": "a", "password": "b"})).status_code \
        == 404
    assert (await client.post(URL, json={"username": "zhangsan", "password": ""})).status_code == 422


async def test_repeated_failures_are_throttled(client, directory):
    for _ in range(10):
        await client.post(URL, json={"username": "zhangsan", "password": "nope"})
    blocked = await client.post(URL, json={"username": "zhangsan", "password": "Corp-Pass-1"})
    assert blocked.status_code == 429
    tried = len(directory.calls)
    assert tried < 11  # 封住以后不再去问目录


async def test_disabled_people_cannot_log_in(client, identity):
    first = (await client.post(URL, json={"username": "zhangsan", "password": "Corp-Pass-1"})).json()
    await identity.users.set_disabled(first["id"], True)
    client.cookies.clear()
    assert (await client.post(URL, json={"username": "zhangsan", "password": "Corp-Pass-1"})).status_code == 401


async def test_the_method_is_listed(client):
    methods = (await client.get("/api/auth/methods")).json()["methods"]
    assert methods == [{"id": "ds_ad", "kind": "ldap", "label": "集团域账号", "style": "password", "url": URL,
                        "inClientAgents": []}]
