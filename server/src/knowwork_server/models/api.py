"""模型接入的 HTTP 接口。

厂商、模型与连通测试是全平台共用的配置（/api/system/...），只给系统管理员：路由上用
access.deps.require_system_admin 判，服务里再判一次。
空间用量（/api/workspaces/{ws}/usage）要求空间 admin（workspace:edit）；个人用量给空间成员。
调用者身份由主线注入（current_principal）。
"""

from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import date
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response

from ..access.deps import require_permission, require_system_admin
from ..access.vocab import WORKSPACE_RESOURCE
from ..auth.principal import Principal
from .core import ADMIN_ACTION, Authorizer
from .errors import Conflict, Denied, Invalid, ModelsError, NotFound, Unavailable
from .probe import DEFAULT_PROBE_TIMEOUT, pick_capability, probe
from .recorder import GroupBy, UsageRecorder
from .schemas import Capability, ModelCreate, ModelUpdate, ProviderCreate, ProviderUpdate
from .service import ModelCatalog

_STATUS = {Denied: 403, NotFound: 404, Conflict: 409, Invalid: 422, Unavailable: 503}
IdPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9_.\-]{1,64}$")]


def _http(error: ModelsError) -> HTTPException:
    for kind, status in _STATUS.items():
        if isinstance(error, kind):
            return HTTPException(status_code=status, detail=str(error))
    return HTTPException(status_code=500, detail="服务暂时不可用")


async def _call(awaitable):
    try:
        return await awaitable
    except ModelsError as error:
        raise _http(error) from error


def build_router(catalog: ModelCatalog, recorder: UsageRecorder, authorizer: Authorizer,
                 current_principal: Callable[..., Awaitable[Principal]], probe_http: httpx.AsyncClient, *,
                 probe_timeout: float = DEFAULT_PROBE_TIMEOUT) -> APIRouter:
    """probe_http 由调用方持有并负责关闭（不要带 base_url 与默认鉴权头）。"""
    router = APIRouter(tags=["models"])
    system = APIRouter(prefix="/api/system")
    System = Annotated[Principal, Depends(require_system_admin(authorizer, current_principal))]
    _providers(system, catalog, System)
    _models(system, catalog, System, probe_http, probe_timeout)
    workspace = APIRouter(prefix="/api/workspaces/{workspace_id}")
    admin = require_permission(authorizer, current_principal, lambda _: WORKSPACE_RESOURCE, ADMIN_ACTION)
    Admin = Annotated[Principal, Depends(admin)]
    Who = Annotated[Principal, Depends(current_principal)]
    _member_routes(workspace, catalog, recorder, Who, Admin)
    router.include_router(system)
    router.include_router(workspace)
    return router


def _providers(router: APIRouter, catalog: ModelCatalog, Admin) -> None:
    @router.get("/model-providers")
    async def list_providers(who: Admin):
        """列出模型厂商"""
        return [asdict(v) for v in await _call(catalog.list_providers(who.user_id))]

    @router.post("/model-providers", status_code=201)
    async def create_provider(body: ProviderCreate, who: Admin):
        """添加模型厂商"""
        return asdict(await _call(catalog.create_provider(who.user_id, body)))

    @router.patch("/model-providers/{provider_id}")
    async def update_provider(provider_id: IdPath, body: ProviderUpdate, who: Admin):
        """修改模型厂商"""
        return asdict(await _call(catalog.update_provider(who.user_id, provider_id, body)))

    @router.delete("/model-providers/{provider_id}", status_code=204)
    async def delete_provider(provider_id: IdPath, who: Admin):
        """删除模型厂商"""
        await _call(catalog.delete_provider(who.user_id, provider_id))
        return Response(status_code=204)


def _models(router: APIRouter, catalog: ModelCatalog, Admin, http: httpx.AsyncClient, timeout: float) -> None:
    @router.get("/models")
    async def list_models(who: Admin):
        """列出模型"""
        return [asdict(v) for v in await _call(catalog.list_models(who.user_id))]

    @router.post("/models", status_code=201)
    async def create_model(body: ModelCreate, who: Admin):
        """添加模型"""
        return asdict(await _call(catalog.create_model(who.user_id, body)))

    @router.patch("/models/{model_id}")
    async def update_model(model_id: IdPath, body: ModelUpdate, who: Admin):
        """修改模型"""
        return asdict(await _call(catalog.update_model(who.user_id, model_id, body)))

    @router.delete("/models/{model_id}", status_code=204)
    async def delete_model(model_id: IdPath, who: Admin):
        """删除模型"""
        await _call(catalog.delete_model(who.user_id, model_id))
        return Response(status_code=204)

    @router.post("/models/{model_id}/test")
    async def test_model(model_id: IdPath, who: Admin,
                         capability: Annotated[Capability | None, Query()] = None):
        """测试模型调用"""
        route, view = await _call(catalog.route_by_id(who.user_id, model_id))
        chosen = capability.value if capability else pick_capability(view.capabilities)
        return asdict(await probe(http, route, chosen, timeout=timeout))


def _member_routes(router: APIRouter, catalog: ModelCatalog, recorder: UsageRecorder, Who, Admin) -> None:
    @router.get("/usage")
    async def usage(workspace_id: IdPath, who: Admin, start: Annotated[date, Query()],
                    end: Annotated[date, Query()], group_by: Annotated[GroupBy, Query()] = GroupBy.USER,
                    user_id: Annotated[str | None, Query(max_length=64)] = None):
        """用量统计（管理员）"""
        rows = await _call(recorder.summary(workspace_id, start, end, group_by, user_id=user_id))
        return [asdict(row) for row in rows]

    @router.get("/usage/me")
    async def my_usage(workspace_id: IdPath, who: Who, start: Annotated[date, Query()],
                       end: Annotated[date, Query()], group_by: Annotated[GroupBy, Query()] = GroupBy.MODEL):
        """我的用量"""
        if group_by == GroupBy.USER:
            raise HTTPException(status_code=422, detail="个人用量只能按模型或按天汇总")
        if not catalog.is_member(who.user_id, workspace_id):
            raise HTTPException(status_code=403, detail="无权限执行该操作")
        rows = await _call(recorder.summary(workspace_id, start, end, group_by, user_id=who.user_id))
        return [asdict(row) for row in rows]
