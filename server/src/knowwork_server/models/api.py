"""模型接入的 HTTP 接口。

管理接口（厂商、模型、分组、连通测试、全空间用量）要求空间 admin：路由上用
access.deps.require_permission 判 workspace:edit，服务里再判一次。
成员接口：自己能用的模型、自己的用量。调用者身份由主线注入（current_principal）。
"""

from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import date
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response

from ..access.deps import require_permission
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
    router = APIRouter(prefix="/api/workspaces/{workspace_id}", tags=["models"])
    admin = require_permission(authorizer, current_principal, lambda _: WORKSPACE_RESOURCE, ADMIN_ACTION)
    Admin = Annotated[Principal, Depends(admin)]
    Who = Annotated[Principal, Depends(current_principal)]
    _providers(router, catalog, Admin)
    _models(router, catalog, Admin, probe_http, probe_timeout)
    _member_routes(router, catalog, recorder, Who, Admin)
    return router


def _providers(router: APIRouter, catalog: ModelCatalog, Admin) -> None:
    @router.get("/model-providers")
    async def list_providers(workspace_id: IdPath, who: Admin):
        return [asdict(v) for v in await _call(catalog.list_providers(who.user_id, workspace_id))]

    @router.post("/model-providers", status_code=201)
    async def create_provider(workspace_id: IdPath, body: ProviderCreate, who: Admin):
        return asdict(await _call(catalog.create_provider(who.user_id, workspace_id, body)))

    @router.patch("/model-providers/{provider_id}")
    async def update_provider(workspace_id: IdPath, provider_id: IdPath, body: ProviderUpdate, who: Admin):
        return asdict(await _call(catalog.update_provider(who.user_id, workspace_id, provider_id, body)))

    @router.delete("/model-providers/{provider_id}", status_code=204)
    async def delete_provider(workspace_id: IdPath, provider_id: IdPath, who: Admin):
        await _call(catalog.delete_provider(who.user_id, workspace_id, provider_id))
        return Response(status_code=204)


def _models(router: APIRouter, catalog: ModelCatalog, Admin, http: httpx.AsyncClient, timeout: float) -> None:
    @router.get("/models")
    async def list_models(workspace_id: IdPath, who: Admin):
        return [asdict(v) for v in await _call(catalog.list_models(who.user_id, workspace_id))]

    @router.post("/models", status_code=201)
    async def create_model(workspace_id: IdPath, body: ModelCreate, who: Admin):
        return asdict(await _call(catalog.create_model(who.user_id, workspace_id, body)))

    @router.patch("/models/{model_id}")
    async def update_model(workspace_id: IdPath, model_id: IdPath, body: ModelUpdate, who: Admin):
        return asdict(await _call(catalog.update_model(who.user_id, workspace_id, model_id, body)))

    @router.delete("/models/{model_id}", status_code=204)
    async def delete_model(workspace_id: IdPath, model_id: IdPath, who: Admin):
        await _call(catalog.delete_model(who.user_id, workspace_id, model_id))
        return Response(status_code=204)

    @router.post("/models/{model_id}/test")
    async def test_model(workspace_id: IdPath, model_id: IdPath, who: Admin,
                         capability: Annotated[Capability | None, Query()] = None):
        route, view = await _call(catalog.route_by_id(who.user_id, workspace_id, model_id))
        chosen = capability.value if capability else pick_capability(view.capabilities)
        return asdict(await probe(http, route, chosen, timeout=timeout))


def _member_routes(router: APIRouter, catalog: ModelCatalog, recorder: UsageRecorder, Who, Admin) -> None:
    @router.get("/usage")
    async def usage(workspace_id: IdPath, who: Admin, start: Annotated[date, Query()],
                    end: Annotated[date, Query()], group_by: Annotated[GroupBy, Query()] = GroupBy.USER,
                    user_id: Annotated[str | None, Query(max_length=64)] = None):
        rows = await _call(recorder.summary(workspace_id, start, end, group_by, user_id=user_id))
        return [asdict(row) for row in rows]

    @router.get("/usage/me")
    async def my_usage(workspace_id: IdPath, who: Who, start: Annotated[date, Query()],
                       end: Annotated[date, Query()], group_by: Annotated[GroupBy, Query()] = GroupBy.MODEL):
        if group_by == GroupBy.USER:
            raise HTTPException(status_code=422, detail="个人用量只能按模型或按天汇总")
        if not catalog.is_member(who.user_id, workspace_id):
            raise HTTPException(status_code=403, detail="无权限执行该操作")
        rows = await _call(recorder.summary(workspace_id, start, end, group_by, user_id=who.user_id))
        return [asdict(row) for row in rows]
