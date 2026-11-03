"""登录方式列表：登录页按它显示入口。各登录方式（含扩展提供的）登记一个来源，某个来源出错时跳过它、不拖垮登录页。"""

import pytest

from knowwork_server.identity.methods import LoginMethod, LoginMethods

pytestmark = pytest.mark.anyio


async def test_sources_are_listed_in_order_and_a_broken_one_is_skipped():
    methods = LoginMethods()
    ldap = LoginMethod(id="ad", kind="ldap", label="域账号", style="password", url="/api/auth/password/ad")

    async def broken():
        raise RuntimeError("来源配置读不出来")

    async def directory():
        return [ldap]

    methods.add(lambda: [LoginMethod(id="oidc", kind="oidc", label="企业账号", style="redirect",
                                     url="/api/auth/oidc/login")])
    methods.add(broken)
    methods.add(directory)
    assert [m.id for m in await methods.list()] == ["oidc", "ad"]


def test_style_and_id_are_validated():
    with pytest.raises(ValueError):
        LoginMethod(id="x", kind="oidc", label="x", style="popup", url="/x")
    with pytest.raises(ValueError):
        LoginMethod(id="bad id", kind="oidc", label="x", style="redirect", url="/x")
    with pytest.raises(ValueError):
        LoginMethod(id="x", kind="oidc", label="x", style="redirect", url="https://evil.example.com/login")
