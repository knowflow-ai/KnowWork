"""知识：知识源接口、KnowFlow 连接器、引用目录与两个内置工具（技术方案 5.4、6.4、8.1、8.3、8.4 节）。

主线整合：
    knowledge = build_knowledge(master_key, sessionmaker, access_service)
    public_app.include_router(build_router(knowledge.admin, current_principal))
    registry = StaticToolRegistry.of([*builtin_tools, *knowledge.tools])
    # 迁移里建 KNOWLEDGE_TABLES 并对 KNOWLEDGE_RLS_TABLES 开行级安全；停机时 await knowledge.aclose()
"""

from dataclasses import dataclass

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..tools.registry import BuiltinTool
from .admin import SourceAdmin
from .api import build_router
from .citation_store import CitationStore
from .connectors import AccessChecker, ConnectorFactory, EmailLookup, user_email_lookup
from .crypto import SecretBox
from .service import KnowledgeService
from .store import SourceStore
from .tables import KNOWLEDGE_RLS_TABLES, KNOWLEDGE_TABLES
from .tools import BoundBases, CatalogOf, knowledge_tools

__all__ = ["KNOWLEDGE_RLS_TABLES", "KNOWLEDGE_TABLES", "KnowledgeModule", "build_knowledge", "build_router"]


@dataclass(frozen=True)
class KnowledgeModule:
    admin: SourceAdmin
    service: KnowledgeService
    citations: CitationStore
    tools: tuple[BuiltinTool, ...]
    client: httpx.AsyncClient

    async def aclose(self) -> None:
        await self.client.aclose()


def build_knowledge(master_key: str, sessionmaker: async_sessionmaker, access: AccessChecker, *,
                    email_of: EmailLookup | None = None, client: httpx.AsyncClient | None = None,
                    catalog_of: CatalogOf | None = None, bound_bases: BoundBases | None = None,
                    citation_limit: int = 500) -> KnowledgeModule:
    """组装知识模块。client 默认不跟随重定向；email_of 默认查账号表；catalog_of 默认按会话 ID 划分引用目录。"""
    http = client or httpx.AsyncClient(follow_redirects=False, timeout=httpx.Timeout(15.0))
    store = SourceStore(sessionmaker, SecretBox.from_master_key(master_key))
    connectors = ConnectorFactory(store, http)
    lookup = email_of or user_email_lookup(sessionmaker)
    service = KnowledgeService(store, access, connectors, lookup)
    citations = CitationStore(sessionmaker, limit=citation_limit)
    options = {k: v for k, v in (("catalog_of", catalog_of), ("bound_bases", bound_bases)) if v is not None}
    tools = knowledge_tools(service, citations, **options)
    return KnowledgeModule(admin=SourceAdmin(store, access, connectors, lookup), service=service,
                           citations=citations, tools=tools, client=http)
