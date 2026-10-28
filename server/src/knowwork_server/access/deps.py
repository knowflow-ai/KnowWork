"""给其他模块用的依赖：在路由上声明「需要对某资源有某档权限」。"""

from collections.abc import Awaitable, Callable, Mapping
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from ..auth.principal import Principal
from .service import AccessService

PathParams = Mapping[str, str]


def _from_path(params: PathParams) -> str:
    return params["workspace_id"]


def require_permission(service: AccessService, current_principal: Callable[..., Awaitable[Principal]],
                       resource_of: Callable[[PathParams], str], action: str,
                       workspace_of: Callable[[PathParams], str] = _from_path):
    """生成 FastAPI 依赖。resource_of 从路径参数算出资源，例如 lambda p: f"skill:{p['skill_id']}"。

    判定为否、参数缺失或算资源出错，一律 403（fail-closed）。
    """

    async def dependency(request: Request, principal: Annotated[Principal, Depends(current_principal)]) -> Principal:
        try:
            workspace_id = workspace_of(request.path_params)
            resource = resource_of(request.path_params)
        except Exception as error:
            raise HTTPException(status_code=403, detail="无权限执行该操作") from error
        if not service.check(principal.user_id, workspace_id, resource, action):
            raise HTTPException(status_code=403, detail="无权限执行该操作")
        return principal

    return dependency


def require_system_admin(service: AccessService, current_principal: Callable[..., Awaitable[Principal]]):
    """生成 FastAPI 依赖：只放系统管理员本人（全平台共用的配置）。API Key 一律不放：它代表某个空间里的调用方，
    不该改到别的空间也在用的东西。"""

    async def dependency(principal: Annotated[Principal, Depends(current_principal)]) -> Principal:
        if principal.is_api_key or not service.is_system_admin(principal.user_id):
            raise HTTPException(status_code=403, detail="无权限执行该操作")
        return principal

    return dependency
