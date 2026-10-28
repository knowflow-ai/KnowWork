"""知识：知识源接口、KnowFlow 连接器、引用目录与智能体的三个知识工具。

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
from .fetch_tool import SessionInput
from .provided import ProvidedSource, ProvidedSources
from .search_log import CurrentTurn, SearchLog
from .service import KnowledgeService, Pinned
from .store import SourceStore
from .tables import KNOWLEDGE_RLS_TABLES, KNOWLEDGE_TABLES
from .tools import CatalogOf, TurnScope, TurnScopeOf, knowledge_tools
from .turn_ledger import TurnLedger

__all__ = ["KNOWLEDGE_RLS_TABLES", "KNOWLEDGE_TABLES", "KnowledgeModule", "Pinned", "ProvidedSource", "ProvidedSources",
           "TurnScope", "build_knowledge", "build_router"]


@dataclass(frozen=True)
class KnowledgeModule:
    admin: SourceAdmin
    service: KnowledgeService
    citations: CitationStore
    tools: tuple[BuiltinTool, ...]
    client: httpx.AsyncClient
    provided: ProvidedSources  # 扩展提供的知识源在业务服务启动时登记进来
    turns: TurnLedger  # 这一轮给过原文的片段；要挂到事件泵的观察者上才知道轮次边界
    search_log: SearchLog  # 检索记录；close_turn 登记到每轮汇总上，删会话时 discard

    async def aclose(self) -> None:
        await self.client.aclose()


def build_knowledge(master_key: str, sessionmaker: async_sessionmaker, access: AccessChecker, *,
                    email_of: EmailLookup | None = None, client: httpx.AsyncClient | None = None,
                    catalog_of: CatalogOf | None = None, turn_scope: TurnScopeOf | None = None,
                    current_turn: CurrentTurn | None = None, citation_limit: int = 500,
                    sandbox: SessionInput | None = None) -> KnowledgeModule:
    """组装知识模块。client 默认不跟随重定向；email_of 默认查账号表；catalog_of 默认按会话 ID 划分引用目录；
    current_turn 告诉检索记录这个会话正在跑第几轮（每轮汇总给），不给时收尾时再补；
    sandbox 是会话沙箱，给了才有 knowledge_fetch（没开 Docker 沙箱时不给）。"""
    http = client or httpx.AsyncClient(follow_redirects=False, timeout=httpx.Timeout(15.0))
    store = SourceStore(sessionmaker, SecretBox.from_master_key(master_key))
    connectors = ConnectorFactory(store, http)
    lookup = email_of or user_email_lookup(sessionmaker)
    provided = ProvidedSources()
    service = KnowledgeService(store, access, connectors, lookup, provided)
    citations = CitationStore(sessionmaker, limit=citation_limit)
    options = {k: v for k, v in (("catalog_of", catalog_of), ("turn_scope", turn_scope)) if v is not None}
    turns = TurnLedger()
    search_log = SearchLog(sessionmaker, current_turn) if current_turn else SearchLog(sessionmaker)
    tools = knowledge_tools(service, citations, ledger=turns, search_log=search_log, sandbox=sandbox, **options)
    return KnowledgeModule(admin=SourceAdmin(store, access, connectors, lookup, provided), service=service,
                           citations=citations, tools=tools, client=http, provided=provided, turns=turns,
                           search_log=search_log)
