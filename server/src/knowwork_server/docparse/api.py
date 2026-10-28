"""文档解析服务的配置接口（「系统设置」里的文档解析）。全平台共用一份，只给系统管理员（同模型管理）。

- GET  /api/system/document-parser        现在用的是哪份配置、界面里存的值（令牌只给末四位）
- PUT  /api/system/document-parser        保存，几秒内生效，不用重启
- POST /api/system/document-parser/test   用表单里的地址与令牌测一次连接
"""

from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends

from ..access.deps import require_system_admin
from ..access.service import AccessService
from ..auth.principal import Principal
from .service import DocumentParsers, ParserSave, ParserTest


def build_router(parsers: DocumentParsers, authorizer: AccessService,
                 current_principal: Callable[..., Awaitable[Principal]]) -> APIRouter:
    router = APIRouter(prefix="/api/system/document-parser", tags=["models"])
    Admin = Annotated[Principal, Depends(require_system_admin(authorizer, current_principal))]

    @router.get("")
    async def view(_: Admin) -> dict:
        """文档解析服务的配置"""
        return asdict(await parsers.view())

    @router.put("")
    async def save(body: ParserSave, who: Admin) -> dict:
        """保存文档解析服务的配置"""
        return asdict(await parsers.save(who.user_id, body))

    @router.post("/test")
    async def test(body: ParserTest, _: Admin) -> dict:
        """测试文档解析服务的连接"""
        return asdict(await parsers.probe(body))

    return router
