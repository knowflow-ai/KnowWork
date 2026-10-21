"""审计事件的批量异步落库，结构照 conversations/writer.py 的 BatchWriter：

后台任务按批取队列、整批插入；整批失败就逐行重试，数据本身存不下的行记日志后丢弃；
连逐行都写不进去（数据库不可用）就退避后整体重试，行一直留在队列里。

与 BatchWriter 的差别只有一处，也是审计的要求：**put 从不阻塞业务请求**。BatchWriter 满了让事件泵等，
因为宿主缓冲兜得住；审计满了就丢弃新事件并记 ERROR 日志、累计 dropped，业务请求照常完成。
行级安全按工作空间分组写：每组一个事务，事务开头声明该组的 workspace_id。
"""

import asyncio
import contextlib
import logging
from collections import defaultdict
from collections.abc import Iterable

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DataError, IntegrityError, ProgrammingError, StatementError
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..access.rls import set_workspace
from .events import AuditEvent
from .models import AuditLog

log = logging.getLogger(__name__)

BATCH_SIZE = 200
MAX_PENDING = 10_000
RETRY_DELAYS = (0.2, 1.0, 5.0, 30.0)
_BAD_ROW_ERRORS = (DataError, IntegrityError, ProgrammingError, StatementError, UnicodeEncodeError, ValueError)


class AuditWriter:
    def __init__(self, sessionmaker: async_sessionmaker, *, max_pending: int = MAX_PENDING,
                 retry_delays: tuple[float, ...] = RETRY_DELAYS) -> None:
        self._sessionmaker = sessionmaker  # 不带工作空间的 sessionmaker；按组声明工作空间
        self._retry_delays = retry_delays
        self._queue: asyncio.Queue[AuditEvent] = asyncio.Queue(maxsize=max_pending)
        self._inflight = 0
        self._task: asyncio.Task | None = None
        self.dropped = 0

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="audit-writer")

    def put(self, event: AuditEvent) -> bool:
        """入队，不等待。队列满时丢弃并返回 False。"""
        try:
            self._queue.put_nowait(event.normalized())
        except asyncio.QueueFull:
            self.dropped += 1
            log.error("audit queue full, dropped event action=%s workspace=%s (dropped=%d)",
                      event.action, event.workspace_id, self.dropped)
            return False
        return True

    @property
    def backlog(self) -> int:
        return self._queue.qsize() + self._inflight

    async def flush(self, timeout: float = 10.0) -> None:
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(timeout):
                while self.backlog:
                    await asyncio.sleep(0.02)

    async def close(self, timeout: float = 10.0) -> None:
        await self.flush(timeout)
        if self.backlog:
            log.error("audit writer closing with %d unwritten events", self.backlog)
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _run(self) -> None:
        while True:
            batch = [await self._queue.get()]
            while len(batch) < BATCH_SIZE and not self._queue.empty():
                batch.append(self._queue.get_nowait())
            self._inflight = len(batch)
            try:
                await self._write_until_done(batch)
            finally:
                self._inflight = 0

    async def _write_until_done(self, batch: list[AuditEvent]) -> None:
        attempt = 0
        while True:
            try:
                await self._insert(batch)
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("audit batch of %d failed, retrying row by row", len(batch), exc_info=True)
            try:
                for event in batch:
                    await self._insert_one(event)
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                delay = self._retry_delays[min(attempt, len(self._retry_delays) - 1)]
                attempt += 1
                log.exception("audit store unavailable, retry in %.1fs", delay)
                await asyncio.sleep(delay)

    async def _insert_one(self, event: AuditEvent) -> None:
        try:
            await self._insert([event])
        except _BAD_ROW_ERRORS:
            log.error("audit event %s (%s) unstorable, dropped", event.id, event.action)

    async def _insert(self, events: Iterable[AuditEvent]) -> None:
        groups: dict[str, list[dict]] = defaultdict(list)
        for event in events:
            groups[event.workspace_id].append(event.row())
        async with self._sessionmaker() as session:
            for workspace_id, rows in groups.items():
                await set_workspace(session, workspace_id)
                await session.execute(insert(AuditLog).values(rows).on_conflict_do_nothing())
            await session.commit()
