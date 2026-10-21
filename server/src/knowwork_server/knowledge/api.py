"""知识源的 HTTP 接口。调用者身份由主线注入（current_principal），权限判定在 SourceAdmin 里。

- GET    /api/workspaces/{ws}/knowledge-sources                  我能用的知识源（按权限给摘要或详情）
- POST   /api/workspaces/{ws}/knowledge-sources                  新建（空间 owner、admin）
- GET    /api/workspaces/{ws}/knowledge-sources/{id}
- PATCH  /api/workspaces/{ws}/knowledge-sources/{id}             修改（edit）
- DELETE /api/workspaces/{ws}/knowledge-sources/{id}             删除（owner）
- POST   /api/workspaces/{ws}/knowledge-sources/{id}/test        以本人身份做连通测试（edit）
- GET    /api/workspaces/{ws}/knowledge-bases                    可编辑的知识源下各有哪些知识库（授权页、知识库页）

错误统一 {"detail": "..."}，文案面向用户、不含内部细节。任何响应都不含密钥明文。
"""

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Response

from ..auth.principal import Principal
from .admin import SourceAdmin
from .errors import KnowledgeError
from .schemas import (
    ProbeResult,
    SourceBases,
    SourceCreate,
    SourceDetail,
    SourcePatch,
    SourceSummary,
)

IdPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9_.\-]{1,64}$")]


async def _call(awaitable):
    try:
        return await awaitable
    except KnowledgeError as error:
        raise HTTPException(status_code=error.status, detail=str(error)) from error


def build_router(admin: SourceAdmin, current_principal: Callable[..., Awaitable[Principal]]) -> APIRouter:
    router = APIRouter(prefix="/api/workspaces/{workspace_id}", tags=["knowledge"])
    Who = Annotated[Principal, Depends(current_principal)]
    S = "/knowledge-sources"

    @router.get("/knowledge-bases", response_model=list[SourceBases])
    async def list_bases(workspace_id: IdPath, who: Who):
        return await _call(admin.catalog(who.user_id, workspace_id))

    @router.get(S, response_model=list[SourceDetail | SourceSummary])
    async def list_sources(workspace_id: IdPath, who: Who):
        return await _call(admin.list(who.user_id, workspace_id))

    @router.post(S, status_code=201, response_model=SourceDetail)
    async def create(workspace_id: IdPath, body: SourceCreate, who: Who):
        return await _call(admin.create(who.user_id, workspace_id, body))

    @router.get(S + "/{source_id}", response_model=SourceDetail | SourceSummary)
    async def get(workspace_id: IdPath, source_id: IdPath, who: Who):
        return await _call(admin.get(who.user_id, workspace_id, source_id))

    @router.patch(S + "/{source_id}", response_model=SourceDetail)
    async def update(workspace_id: IdPath, source_id: IdPath, body: SourcePatch, who: Who):
        return await _call(admin.update(who.user_id, workspace_id, source_id, body))

    @router.delete(S + "/{source_id}", status_code=204)
    async def delete(workspace_id: IdPath, source_id: IdPath, who: Who):
        await _call(admin.delete(who.user_id, workspace_id, source_id))
        return Response(status_code=204)

    @router.post(S + "/{source_id}/test", response_model=ProbeResult)
    async def probe(workspace_id: IdPath, source_id: IdPath, who: Who):
        return await _call(admin.probe(who.user_id, workspace_id, source_id))

    return router
