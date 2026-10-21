"""事件的批量异步落库。

事件泵先分配序号、推给浏览器，再把这一帧交给这里；后台任务按批提交，不阻塞实时推送。
尚未提交的帧留在 pending 里，回放时与库里的行合并（见 replay.py），因此推送与落库的先后不会漏事件。

- 一批失败时退避后逐行重试：数据本身存不下的行（DataError 等）换成 kind=kernel.invalid 的占位行，
  序号不变；连占位都写不进去（数据库不可用）就整体退避重试，帧一直留在 pending 里；
- pending 有上限，满了 put 会等，事件泵随之放慢读宿主，宿主缓冲兜住这段时间。
"""

import asyncio
import contextlib
import logging
from collections import OrderedDict
from dataclasses import dataclass

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DataError, IntegrityError, ProgrammingError, StatementError
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..kernel.contract import INVALID_FRAME_KIND
from .listeners import StoredEvent
from .models import RunEvent

log = logging.getLogger(__name__)

BATCH_SIZE = 200
MAX_PENDING = 10_000
RETRY_DELAYS = (0.2, 1.0, 5.0, 30.0)
_BAD_ROW_ERRORS = (DataError, IntegrityError, ProgrammingError, StatementError, UnicodeEncodeError, ValueError)


@dataclass(frozen=True)
class PendingRow:
    event: StoredEvent
    workspace_id: str
    host_epoch: str
    host_cursor: int

    def values(self) -> dict:
        return {"conversation_id": self.event.conversation_id, "seq": self.event.seq, "workspace_id": self.workspace_id,
                "host_epoch": self.host_epoch, "host_cursor": self.host_cursor, "kind": self.event.kind,
                "data": self.event.data, "ui": list(self.event.ui)}

    def placeholder(self) -> "PendingRow":
        stub = StoredEvent(conversation_id=self.event.conversation_id, seq=self.event.seq, kind=INVALID_FRAME_KIND,
                           data={"reason": "unstorable", "kind": self.event.kind[:64]}, ui=self.event.ui)
        return PendingRow(event=stub, workspace_id=self.workspace_id, host_epoch=self.host_epoch,
                          host_cursor=self.host_cursor)


class BatchWriter:
    def __init__(self, sessionmaker: async_sessionmaker, *, max_pending: int = MAX_PENDING,
                 retry_delays: tuple[float, ...] = RETRY_DELAYS) -> None:
        self._sessionmaker = sessionmaker
        self._max_pending = max_pending
        self._retry_delays = retry_delays
        self._pending: dict[str, OrderedDict[int, PendingRow]] = {}
        self._queue: asyncio.Queue[PendingRow] = asyncio.Queue()
        self._room = asyncio.Condition()
        self._size = 0
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="run-event-writer")

    async def put(self, row: PendingRow) -> None:
        async with self._room:
            await self._room.wait_for(lambda: self._size < self._max_pending)
            self._size += 1
        self._pending.setdefault(row.event.conversation_id, OrderedDict())[row.event.seq] = row
        self._queue.put_nowait(row)

    def pending(self, conversation_id: str, after: int = 0) -> list[StoredEvent]:
        """尚未提交的帧快照（按序号）。同步调用，与登记监听者处在同一个事件循环片里。"""
        rows = self._pending.get(conversation_id)
        return [] if not rows else [r.event for seq, r in rows.items() if seq > after]

    async def flush(self, timeout: float = 10.0) -> None:
        """等到当前所有待写帧都提交（或超时）。"""
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(timeout):
                while self._size:
                    await asyncio.sleep(0.02)

    async def close(self, timeout: float = 10.0) -> None:
        await self.flush(timeout)
        if self._size:
            log.error("run event writer closing with %d unwritten frames", self._size)
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _run(self) -> None:
        while True:
            batch = [await self._queue.get()]
            while len(batch) < BATCH_SIZE and not self._queue.empty():
                batch.append(self._queue.get_nowait())
            await self._write_until_done(batch)
            await self._release(batch)

    async def _write_until_done(self, batch: list[PendingRow]) -> None:
        attempt = 0
        while True:
            try:
                await self._insert(batch)
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("run event batch of %d failed, retrying row by row", len(batch), exc_info=True)
            try:
                for row in batch:
                    await self._insert_one(row)
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                delay = self._retry_delays[min(attempt, len(self._retry_delays) - 1)]
                attempt += 1
                log.exception("run event store unavailable, retry in %.1fs", delay)
                await asyncio.sleep(delay)

    async def _insert_one(self, row: PendingRow) -> None:
        try:
            await self._insert([row])
        except _BAD_ROW_ERRORS:
            log.error("run event %s/%s unstorable, writing placeholder", row.event.conversation_id, row.event.seq)
            try:
                await self._insert([row.placeholder()])
            except _BAD_ROW_ERRORS:
                # 占位也存不下只可能是会话已被删除（外键），这帧没有归属，丢弃。
                log.error("run event %s/%s placeholder rejected, dropped", row.event.conversation_id, row.event.seq)

    async def _insert(self, rows: list[PendingRow]) -> None:
        statement = insert(RunEvent).values([r.values() for r in rows]).on_conflict_do_nothing()
        async with self._sessionmaker() as session:
            await session.execute(statement)
            await session.commit()

    async def _release(self, batch: list[PendingRow]) -> None:
        for row in batch:
            rows = self._pending.get(row.event.conversation_id)
            if rows is not None:
                rows.pop(row.event.seq, None)
                if not rows:
                    del self._pending[row.event.conversation_id]
        async with self._room:
            self._size -= len(batch)
            self._room.notify_all()
