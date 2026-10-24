"""系统设置接口。

- GET    /api/brand            → 品牌名称与 Logo 地址，不用登录（登录页也要显示）
- GET    /api/brand/logo       → 自定义 Logo 图片，没有时 404（前端用默认 Logo）
- GET    /api/system/check     → 当前用户是不是系统管理员（决定要不要显示「系统设置」入口）
- GET    /api/system/info      → 系统信息
- PUT    /api/system/brand     → 改名称（空表示恢复默认）
- PUT    /api/system/brand/logo → 上传 Logo
- DELETE /api/system/brand/logo → 恢复默认 Logo

修改只能在网页上做：API Key 一律拒绝，系统级配置不该交给脚本。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field

from ..auth.principal import Principal
from .brand import MAX_LOGO_BYTES, Brand, BrandService, Forbidden, InvalidBrand
from .info import SystemInfoService

NOT_ADMIN = "只有系统管理员能修改系统设置"
NO_API_KEY = "系统设置只能在网页上修改"
LOGO_PATH = "/api/brand/logo"
# 自定义 Logo 可能是 SVG：沙箱化，直接打开也不会执行脚本、加载外部资源
LOGO_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
    "X-Content-Type-Options": "nosniff",
    # 地址里带版本号，换了 Logo 就是新地址，可以放心缓存
    "Cache-Control": "public, max-age=86400",
}


class NameBody(BaseModel):
    name: str | None = Field(default=None, max_length=200)


def _brand_json(brand: Brand) -> dict:
    logo_url = f"{LOGO_PATH}?v={brand.logo_version}" if brand.logo_version else None
    return {"name": brand.name, "logo_url": logo_url}


def build_router(brand: BrandService, info: SystemInfoService, principal) -> APIRouter:
    router = APIRouter(tags=["system"])
    Who = Annotated[Principal, Depends(principal)]

    async def admin(who: Who) -> str:
        if who.is_api_key:
            raise HTTPException(status_code=403, detail=NO_API_KEY)
        if not await brand.is_admin(who.user_id):
            raise HTTPException(status_code=403, detail=NOT_ADMIN)
        return who.user_id

    Admin = Annotated[str, Depends(admin)]

    async def run(call):
        try:
            return _brand_json(await call)
        except Forbidden as error:
            raise HTTPException(status_code=403, detail=NOT_ADMIN) from error
        except InvalidBrand as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.get("/api/brand")
    async def get_brand() -> dict:
        return _brand_json(await brand.brand())

    @router.get(LOGO_PATH)
    async def get_logo() -> Response:
        found = await brand.logo()
        if found is None:
            raise HTTPException(status_code=404, detail="没有自定义 Logo")
        data, media_type = found
        return Response(content=data, media_type=media_type, headers=LOGO_HEADERS)

    @router.get("/api/system/check")
    async def check(who: Who) -> dict:
        return {"allowed": not who.is_api_key and await brand.is_admin(who.user_id)}

    @router.get("/api/system/info")
    async def get_info(_: Admin) -> dict:
        found = await info.info()
        return {"knowwork": found.knowwork, "dsh": found.dsh, "database": found.database, "license": found.license,
                "extensions": [{"name": e.name, "enabled": e.enabled, "reason": e.reason} for e in found.extensions]}

    @router.put("/api/system/brand")
    async def rename(body: NameBody, actor: Admin) -> dict:
        return await run(brand.rename(actor, body.name))

    @router.put("/api/system/brand/logo")
    async def upload_logo(file: UploadFile, actor: Admin) -> dict:
        # 多读 1 字节就能判断是否超限，不把大文件整个读进内存
        data = await file.read(MAX_LOGO_BYTES + 1)
        return await run(brand.set_logo(actor, data))

    @router.delete("/api/system/brand/logo")
    async def reset_logo(actor: Admin) -> dict:
        return await run(brand.clear_logo(actor))

    return router
