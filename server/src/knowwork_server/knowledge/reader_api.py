"""对外检索的 REST 接口，API Key 要带 knowledge:read 范围，按 Key 所属用户的授权收窄。

- GET  /api/v1/knowledge-bases          本人能查的知识库（说明、字段、是否支持按字段筛选）
- POST /api/v1/knowledge/search         检索，参数同 knowledge_search
- POST /api/v1/knowledge/read           阅读，参数同 knowledge_read（chunk 填检索结果里的 span_ref）

检索与阅读是 POST，审计中间件照常记；错误统一 {"detail": "..."}，文案面向用户。
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from .errors import KnowledgeError
from .reader import KnowledgeReader, ReadBody, SearchBody


def build_router(reader: KnowledgeReader, actor: Any) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["knowledge-read"])
    Who = Annotated[Any, Depends(actor)]

    async def caller_of(who):
        return await reader.service.caller(who.workspace_id, who.user_id)

    async def guarded(call):
        try:
            return await call
        except KnowledgeError as error:
            raise HTTPException(status_code=error.status, detail=str(error)) from error

    @router.get("/knowledge-bases")
    async def list_bases(who: Who, knowledge_bases: Annotated[list[str] | None, Query(max_length=50)] = None) -> dict:
        """列出能查的知识库"""
        return await guarded(reader.bases(await caller_of(who), knowledge_bases))

    @router.post("/knowledge/search")
    async def search(body: SearchBody, who: Who) -> dict:
        """检索知识库"""
        return await guarded(reader.search(await caller_of(who), body))

    @router.post("/knowledge/read")
    async def read(body: ReadBody, who: Who) -> dict:
        """阅读文档原文"""
        return await guarded(reader.read(await caller_of(who), body))

    return router
