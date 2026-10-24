"""文档解析服务的配置接口（管理后台「模型 → 文档解析」），只给空间管理员（同模型管理）。

- GET  /api/workspaces/{ws}/document-parser        现在用的是哪份配置、界面里存的值（令牌只给末四位）
- PUT  /api/workspaces/{ws}/document-parser        保存，几秒内生效，不用重启
- POST /api/workspaces/{ws}/document-parser/test   用表单里的地址与令牌测一次连接
"""

from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, Path

from ..access.deps import require_permission
from ..access.service import AccessService
from ..access.vocab import WORKSPACE_RESOURCE
from ..auth.principal import Principal
from ..models.core import ADMIN_ACTION
from .service import DocumentParsers, ParserSave, ParserTest

IdPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9_.\-]{1,64}$")]


def build_router(parsers: DocumentParsers, authorizer: AccessService,
                 current_principal: Callable[..., Awaitable[Principal]]) -> APIRouter:
    router = APIRouter(prefix="/api/workspaces/{workspace_id}/document-parser", tags=["models"])
    admin = require_permission(authorizer, current_principal, lambda _: WORKSPACE_RESOURCE, ADMIN_ACTION)
    Admin = Annotated[Principal, Depends(admin)]

    @router.get("")
    async def view(workspace_id: IdPath, _: Admin) -> dict:
        return asdict(await parsers.view(workspace_id))

    @router.put("")
    async def save(workspace_id: IdPath, body: ParserSave, who: Admin) -> dict:
        return asdict(await parsers.save(who.user_id, workspace_id, body))

    @router.post("/test")
    async def test(workspace_id: IdPath, body: ParserTest, _: Admin) -> dict:
        return asdict(await parsers.probe(workspace_id, body))

    return router
