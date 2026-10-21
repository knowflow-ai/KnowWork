"""审计模块的装配：一个批量写入器、一个 sink、查询、工具回调与中间件参数。"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..tools.service import ToolAuditHook
from .agent import tool_audit_hook
from .middleware import AuditMiddleware
from .query import AuditQuery
from .retention import DEFAULT_BATCH_SIZE, DEFAULT_RETENTION_DAYS, PurgeReport, purge_expired
from .sink import AuditSink, DatabaseAuditSink, FanoutSink
from .writer import AuditWriter


@dataclass(frozen=True)
class AuditModule:
    writer: AuditWriter
    sink: AuditSink
    query: AuditQuery
    tool_hook: ToolAuditHook
    default_workspace_id: str
    sessionmaker: async_sessionmaker

    def start(self) -> None:
        self.writer.start()

    async def close(self) -> None:
        await self.writer.close()

    def install(self, app: FastAPI, *, workspace_of: Callable[[dict[str, Any]], str | None] | None = None) -> None:
        """给公开应用挂上人的操作审计。内部应用（宿主回调）不挂：那里的动作由工具回调记成 Agent 操作。"""
        app.add_middleware(AuditMiddleware, sink=self.sink, default_workspace_id=self.default_workspace_id,
                           workspace_of=workspace_of)

    async def purge(self, *, retention_days: int = DEFAULT_RETENTION_DAYS,
                    batch_size: int = DEFAULT_BATCH_SIZE) -> PurgeReport:
        return await purge_expired(self.sessionmaker, retention_days=retention_days, batch_size=batch_size)


def build_audit(sessionmaker: async_sessionmaker, *, default_workspace_id: str,
                extra_sinks: tuple[AuditSink, ...] = ()) -> AuditModule:
    """sessionmaker 用不带工作空间的那个（主线的 global_sessionmaker）：写入与清理按事件逐组声明空间。

    extra_sinks 留给商业版：写库之外再外发，任一路失败不影响其他路。
    """
    writer = AuditWriter(sessionmaker)
    database = DatabaseAuditSink(writer)
    sink: AuditSink = FanoutSink((database, *extra_sinks)) if extra_sinks else database
    return AuditModule(writer=writer, sink=sink, query=AuditQuery(sessionmaker), tool_hook=tool_audit_hook(sink),
                       default_workspace_id=default_workspace_id, sessionmaker=sessionmaker)
