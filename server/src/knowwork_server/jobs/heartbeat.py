"""worker 心跳（运行面板用）：每 15 秒把自己的池与执行槽、在跑几个、注册的任务类型、下游闸门占用写进 job_worker。

- 面板按心跳时间判断 worker 在不在：超过 HEARTBEAT_STALE 秒没写就标「失联」；
- 正常停机时删掉自己那行；被强杀的留着，过 FORGET_AFTER 由还活着的 worker 顺手清掉；
- 写心跳失败只记日志，不影响任务执行（数据库一时连不上，下一轮再写）。
"""

import asyncio
import logging
import os
import socket
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from .gate import Gate
from .models import JobWorker
from .runner import JobRunner, wait_any

log = logging.getLogger(__name__)

HEARTBEAT_SECONDS = 15.0
HEARTBEAT_STALE = 60.0
FORGET_AFTER = timedelta(days=1)


def _now() -> datetime:
    return datetime.now(UTC)


def worker_id() -> str:
    return f"{socket.gethostname()[:40]}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


class Heartbeat:
    def __init__(self, sessionmaker: async_sessionmaker, runner: JobRunner, gate: Gate, *, version: str,
                 every: float = HEARTBEAT_SECONDS, clock: Callable[[], datetime] = _now) -> None:
        self._sessionmaker = sessionmaker
        self._runner = runner
        self._gate = gate
        self._version = version
        self._every = every
        self._clock = clock
        self.id = worker_id()
        self._started = clock()

    async def run(self, stop: asyncio.Event) -> None:
        try:
            while not stop.is_set():
                try:
                    await self.beat()
                except Exception:
                    log.warning("writing worker heartbeat failed", exc_info=True)
                await wait_any(stop, timeout=self._every)
        finally:
            try:
                await self.remove()
            except Exception:
                log.warning("removing worker heartbeat failed", exc_info=True)

    async def beat(self) -> None:
        now = self._clock()
        values = {"host": socket.gethostname()[:255], "pid": os.getpid(), "version": self._version[:32],
                  "pools": self._runner.pools, "busy": self._runner.busy(), "kinds": self._kinds(),
                  "gates": {name: {"holding": use.holding, "limit": use.limit, "shared": use.shared}
                            for name, use in self._gate.snapshot().items()},
                  "heartbeat_at": now}
        stmt = insert(JobWorker).values(id=self.id, started_at=self._started, **values)
        async with self._sessionmaker() as session, session.begin():
            await session.execute(stmt.on_conflict_do_update(index_elements=[JobWorker.id], set_=values))
            await session.execute(delete(JobWorker).where(JobWorker.heartbeat_at < now - FORGET_AFTER))

    async def remove(self) -> None:
        async with self._sessionmaker() as session, session.begin():
            await session.execute(delete(JobWorker).where(JobWorker.id == self.id))

    def _kinds(self) -> list[dict]:
        return [{"name": kind.name, "label": kind.label or kind.name, "pool": kind.pool,
                 "cancelable": kind.cancelable}
                for kind in sorted(self._runner.kinds, key=lambda k: (k.pool, k.name))]
