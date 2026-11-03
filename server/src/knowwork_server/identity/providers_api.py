"""系统设置「登录方式」的接口：后台配置的 OIDC、通用 OAuth2 的增删改，只给系统管理员。字段名用下划线，同系统设置的其他接口。

- GET    /api/system/login-providers        全部登录方式（环境变量配的 OIDC 排在最前、只读）；密钥只给末四位
- POST   /api/system/login-providers        新建
- PATCH  /api/system/login-providers/{id}   改（不传的字段保持原样，不传密钥保持原密钥）
- DELETE /api/system/login-providers/{id}   删除
"""

from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Response

from ..auth.principal import Principal
from .errors import Conflict, InvalidInput, NotFound
from .provider_schemas import ProviderCreate, ProviderUpdate
from .providers import LoginProviders

ProviderId = Annotated[str, Path(pattern=r"^[A-Za-z0-9_.\-]{1,64}$")]
_STATUS = {NotFound: 404, Conflict: 409, InvalidInput: 422}


def _translate(error: Exception) -> HTTPException:
    return HTTPException(status_code=_STATUS[type(error)], detail=str(error))


def build_router(providers: LoginProviders, system_admin: Callable[..., Awaitable[Principal]]) -> APIRouter:
    router = APIRouter(prefix="/api/system/login-providers")
    Admin = Annotated[Principal, Depends(system_admin)]

    @router.get("")
    async def list_providers(_: Admin) -> dict:
        """登录方式列表"""
        return {"providers": [asdict(view) for view in await providers.views()]}

    @router.post("", status_code=201)
    async def create_provider(body: ProviderCreate, _: Admin) -> dict:
        """新建登录方式

        OIDC 填发行方地址与客户端；通用 OAuth2 填授权、令牌、用户信息三个地址与字段路径。保存后把返回的回调地址登记到对方。
        """
        try:
            return asdict(await providers.create(body))
        except (InvalidInput, Conflict) as error:
            raise _translate(error) from error

    @router.patch("/{provider_id}")
    async def update_provider(provider_id: ProviderId, body: ProviderUpdate, _: Admin) -> dict:
        """修改登录方式"""
        try:
            return asdict(await providers.update(provider_id, body))
        except (InvalidInput, NotFound, Conflict) as error:
            raise _translate(error) from error

    @router.delete("/{provider_id}", status_code=204)
    async def delete_provider(provider_id: ProviderId, _: Admin) -> Response:
        """删除登录方式

        用它登录过的人账号照旧保留。
        """
        try:
            await providers.delete(provider_id)
        except (NotFound, Conflict) as error:
            raise _translate(error) from error
        return Response(status_code=204)

    return router
