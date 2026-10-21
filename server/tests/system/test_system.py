"""系统设置：品牌的读取与修改（只有系统管理员能改）、Logo 校验、系统信息。"""

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request

from knowwork_server.auth.principal import Principal
from knowwork_server.ops.health import host_dsh_version
from knowwork_server.system import (
    DEFAULT_NAME,
    BrandService,
    Forbidden,
    InvalidBrand,
    SystemInfoService,
    build_router,
)

pytestmark = pytest.mark.anyio

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64
SVG = b'<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10"/></svg>'


async def only_admin(user_id: str) -> bool:
    return user_id == "admin"


@pytest.fixture
def brand(sessionmaker) -> BrandService:
    return BrandService(sessionmaker, only_admin)


async def test_default_brand_is_knowwork_without_custom_logo(brand):
    found = await brand.brand()
    assert (found.name, found.logo_version) == (DEFAULT_NAME, None)
    assert await brand.logo() is None


async def test_admin_renames_and_blank_restores_default(brand):
    assert (await brand.rename("admin", "  星河智能  ")).name == "星河智能"
    assert (await brand.rename("admin", "   ")).name == DEFAULT_NAME
    with pytest.raises(InvalidBrand, match="40"):
        await brand.rename("admin", "长" * 41)
    with pytest.raises(InvalidBrand, match="尖括号"):
        await brand.rename("admin", "<b>x</b>")
    with pytest.raises(Forbidden):
        await brand.rename("someone", "别人改的")


async def test_logo_type_is_checked_by_content_and_can_be_reset(brand):
    first = await brand.set_logo("admin", PNG)
    assert first.logo_version is not None
    assert await brand.logo() == (PNG, "image/png")
    assert (await brand.set_logo("admin", SVG)).logo_version is not None
    assert (await brand.logo())[1] == "image/svg+xml"  # type: ignore[index]
    for bad, reason in [
        (b"GIF89a....", "只支持"),
        (b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', "脚本"),
        (b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"></svg>', "脚本"),
        (b'<!DOCTYPE svg [<!ENTITY x "y">]><svg></svg>', "只支持"),
        (b"\x89PNG\r\n\x1a\n" + b"\0" * (512 * 1024), "512 KB"),
        (b"", "没有收到"),
    ]:
        with pytest.raises(InvalidBrand, match=reason):
            await brand.set_logo("admin", bad)
    assert (await brand.clear_logo("admin")).logo_version is None
    assert await brand.logo() is None
    with pytest.raises(Forbidden):
        await brand.set_logo("someone", PNG)


async def test_info_reports_unknown_parts_instead_of_failing(sessionmaker):
    async def broken() -> str | None:
        raise RuntimeError("宿主没起来")

    info = await SystemInfoService("1.2.3", sessionmaker, broken).info()
    # 测试库用 create_all 建表，没有迁移记录
    assert (info.knowwork, info.dsh, info.database) == ("1.2.3", None, None)


async def test_host_dsh_version_reads_health():
    def reply(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "epoch": "e", "dsh": "0.2.1-rc.2"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply), base_url="http://host") as client:
        assert await host_dsh_version(client)() == "0.2.1-rc.2"

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True})),
                                 base_url="http://host") as client:
        assert await host_dsh_version(client)() is None


async def fake_principal(request: Request) -> Principal:
    user = request.headers.get("x-user")
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    method = "api_key" if request.headers.get("x-key") else "session"
    return Principal(user_id=user, auth_method=method, scopes=frozenset() if method == "api_key" else None)


@pytest.fixture
def client(sessionmaker, brand):
    async def dsh() -> str | None:
        return "0.2.1-rc.2"

    app = FastAPI()
    app.include_router(build_router(brand, SystemInfoService("1.0.0", sessionmaker, dsh), fake_principal))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


ADMIN = {"x-user": "admin"}


async def test_brand_is_public_and_only_system_admin_can_change_it(client):
    async with client:
        assert (await client.get("/api/brand")).json() == {"name": DEFAULT_NAME, "logo_url": None}
        assert (await client.get("/api/brand/logo")).status_code == 404

        assert (await client.get("/api/system/check", headers={"x-user": "u2"})).json() == {"allowed": False}
        assert (await client.get("/api/system/check", headers=ADMIN)).json() == {"allowed": True}
        assert (await client.get("/api/system/check")).status_code == 401

        denied = await client.put("/api/system/brand", json={"name": "别人"}, headers={"x-user": "u2"})
        assert denied.status_code == 403
        by_key = await client.put("/api/system/brand", json={"name": "脚本"}, headers={**ADMIN, "x-key": "1"})
        assert (by_key.status_code, by_key.json()["detail"]) == (403, "系统设置只能在网页上修改")

        renamed = await client.put("/api/system/brand", json={"name": "星河智能"}, headers=ADMIN)
        assert renamed.json() == {"name": "星河智能", "logo_url": None}
        assert (await client.put("/api/system/brand", json={"name": "x" * 41}, headers=ADMIN)).status_code == 422


async def test_logo_upload_download_and_reset(client):
    async with client:
        uploaded = await client.put("/api/system/brand/logo", files={"file": ("logo.svg", SVG, "image/png")},
                                    headers=ADMIN)
        url = uploaded.json()["logo_url"]
        assert url.startswith("/api/brand/logo?v=")
        assert (await client.get("/api/brand")).json()["logo_url"] == url
        logo = await client.get(url)
        # 按内容认出是 SVG，不信上传时声明的类型；SVG 下载带沙箱，直接打开也执行不了脚本
        assert logo.headers["content-type"] == "image/svg+xml"
        assert "sandbox" in logo.headers["content-security-policy"]
        assert logo.headers["x-content-type-options"] == "nosniff"

        bad = await client.put("/api/system/brand/logo", files={"file": ("x.gif", b"GIF89a", "image/gif")},
                               headers=ADMIN)
        assert bad.status_code == 422
        assert (await client.delete("/api/system/brand/logo", headers=ADMIN)).json()["logo_url"] is None


async def test_system_info_is_admin_only(client):
    async with client:
        assert (await client.get("/api/system/info", headers={"x-user": "u2"})).status_code == 403
        info = (await client.get("/api/system/info", headers=ADMIN)).json()
        assert info == {"knowwork": "1.0.0", "dsh": "0.2.1-rc.2", "database": None}
