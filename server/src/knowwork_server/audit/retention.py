"""审计保留期清理：默认 90 天（F-AUD-1），按批删除，免得一条大 DELETE 长时间锁表、撑大 WAL。

由 worker 每日调用 purge_expired。audit_log 开了行级安全，应用账号一次只能看见一个工作空间，
所以逐个工作空间清理：先从全局的 workspace 表取空间列表，再在每个空间的事务里声明 workspace_id。
"""

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..access.models import Workspace
from ..access.rls import set_workspace
from .models import AuditLog

log = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = 90
DEFAULT_BATCH_SIZE = 5_000
MAX_BATCHES_PER_WORKSPACE = 1_000  # 防失控：单个空间一次最多删 500 万行，剩下的留给下一次


@dataclass(frozen=True)
class PurgeReport:
    cutoff: datetime
    deleted: dict[str, int]

    @property
    def total(self) -> int:
        return sum(self.deleted.values())


def _validate(retention_days: int, batch_size: int) -> None:
    if not isinstance(retention_days, int) or retention_days < 1:
        raise ValueError("保留天数必须是不小于 1 的整数")
    if not isinstance(batch_size, int) or not 1 <= batch_size <= 100_000:
        raise ValueError("每批删除行数必须在 1 到 100000 之间")


async def _workspaces(sessionmaker: async_sessionmaker) -> list[str]:
    async with sessionmaker() as session:
        return list((await session.execute(select(Workspace.id).order_by(Workspace.id))).scalars())


async def _purge_batch(sessionmaker: async_sessionmaker, workspace_id: str, cutoff: datetime, size: int) -> int:
    victims = (select(AuditLog.id).where(AuditLog.workspace_id == workspace_id, AuditLog.occurred_at < cutoff)
               .limit(size).scalar_subquery())
    async with sessionmaker() as session:
        await set_workspace(session, workspace_id)
        result = await session.execute(delete(AuditLog).where(AuditLog.id.in_(victims)))
        await session.commit()
    return result.rowcount or 0


async def purge_workspace(sessionmaker: async_sessionmaker, workspace_id: str, cutoff: datetime, *,
                          batch_size: int = DEFAULT_BATCH_SIZE) -> int:
    deleted = 0
    for _ in range(MAX_BATCHES_PER_WORKSPACE):
        count = await _purge_batch(sessionmaker, workspace_id, cutoff, batch_size)
        deleted += count
        if count < batch_size:
            break
    return deleted


async def purge_expired(sessionmaker: async_sessionmaker, *, retention_days: int = DEFAULT_RETENTION_DAYS,
                        batch_size: int = DEFAULT_BATCH_SIZE, workspace_ids: Iterable[str] | None = None,
                        now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> PurgeReport:
    """删除早于 now - retention_days 的审计记录。某个空间失败不影响其他空间，失败记日志后继续。"""
    _validate(retention_days, batch_size)
    cutoff = now() - timedelta(days=retention_days)
    targets = list(workspace_ids) if workspace_ids is not None else await _workspaces(sessionmaker)
    deleted: dict[str, int] = {}
    for workspace_id in targets:
        try:
            deleted[workspace_id] = await purge_workspace(sessionmaker, workspace_id, cutoff, batch_size=batch_size)
        except Exception:
            log.exception("audit purge failed for workspace %s", workspace_id)
    log.info("audit purge done: cutoff=%s deleted=%d", cutoff.isoformat(), sum(deleted.values()))
    return PurgeReport(cutoff=cutoff, deleted=deleted)
