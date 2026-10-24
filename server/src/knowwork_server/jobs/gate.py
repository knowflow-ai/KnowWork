"""下游并发闸门：MinerU 这类下游同时只能接几个请求，多个 worker 进程要共用一个名额池。

- Redis 版：有序集合做计数信号量，成员是一次占位、分数是最近一次续期的时间。先清掉过期的，再用乐观锁
  （WATCH + 事务）在有空位时加入自己，没空位就稍后再试；占着的定时续期，worker 崩溃后它的占位过了租期自动失效；
- Redis 读写超时（500 毫秒）或出错时退回进程内信号量：并发按单进程算，宁可慢也不出错，任务照常从 PostgreSQL 领取；
- 只管「同时几个」，不管排队顺序：任务的排队与优先级在任务表里；
- 每个进程记着自己此刻占了几个、上限多少、走没走 Redis（snapshot），worker 心跳带上它，运行面板把各 worker 的加起来。
"""

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

from redis.asyncio import Redis
from redis.exceptions import RedisError, WatchError

log = logging.getLogger(__name__)
PREFIX = "kw:gate:"
LEASE_SECONDS = 120.0
POLL_SECONDS = 0.5
OP_TIMEOUT = 0.5


@dataclass(frozen=True)
class GateUse:
    holding: int   # 这个进程此刻占着几个
    limit: int     # 上限：shared 时是所有 worker 共用的，否则是这个进程自己的
    shared: bool   # 最近一次是不是经 Redis 拿的名额


class Gate(Protocol):
    def hold(self, name: str, limit: int) -> contextlib.AbstractAsyncContextManager[None]: ...

    def snapshot(self) -> dict[str, GateUse]: ...


class _Tally:
    """记这个进程各闸门的占用、上限与是否共用（只在事件循环里改，不用加锁）。"""

    def __init__(self) -> None:
        self._uses: dict[str, GateUse] = {}

    @contextlib.contextmanager
    def held(self, name: str, limit: int, shared: bool):
        self._add(name, limit, shared, 1)
        try:
            yield
        finally:
            self._add(name, limit, shared, -1)

    def _add(self, name: str, limit: int, shared: bool, delta: int) -> None:
        holding = self._uses[name].holding if name in self._uses else 0
        self._uses = {**self._uses, name: GateUse(holding + delta, limit, shared)}

    def snapshot(self) -> dict[str, GateUse]:
        return dict(self._uses)


class LocalGate:
    """进程内的闸门：Redis 不可用时的退路，也是没配 Redis 时的默认。"""

    def __init__(self) -> None:
        self._semaphores: dict[tuple[str, int], asyncio.Semaphore] = {}
        self._tally = _Tally()

    @contextlib.asynccontextmanager
    async def hold(self, name: str, limit: int) -> AsyncIterator[None]:
        semaphore = self._semaphores.setdefault((name, limit), asyncio.Semaphore(max(1, limit)))
        async with semaphore:
            with self._tally.held(name, limit, shared=False):
                yield

    def snapshot(self) -> dict[str, GateUse]:
        return self._tally.snapshot()


class RedisGate:
    def __init__(self, client: Redis, *, lease_seconds: float = LEASE_SECONDS, poll_seconds: float = POLL_SECONDS,
                 op_timeout: float = OP_TIMEOUT) -> None:
        self._client = client
        self._lease = lease_seconds
        self._poll = poll_seconds
        self._timeout = op_timeout
        self._fallback = LocalGate()
        self._tally = _Tally()

    @contextlib.asynccontextmanager
    async def hold(self, name: str, limit: int) -> AsyncIterator[None]:
        key, member = PREFIX + name, uuid.uuid4().hex
        try:
            await self._acquire(key, member, max(1, limit))
        except (RedisError, TimeoutError, OSError):
            log.warning("redis gate %s unavailable, falling back to an in-process gate", name, exc_info=True)
            async with self._fallback.hold(name, limit):
                with self._tally.held(name, limit, shared=False):
                    yield
            return
        refresher = asyncio.create_task(self._refresh(key, member))
        try:
            with self._tally.held(name, limit, shared=True):
                yield
        finally:
            refresher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await refresher
            with contextlib.suppress(RedisError, TimeoutError, OSError):
                await asyncio.wait_for(self._client.zrem(key, member), self._timeout)

    async def _acquire(self, key: str, member: str, limit: int) -> None:
        """乐观锁：WATCH 这个键、数一下占着的，有空位才在事务里加入自己；期间别人改了这个键事务会失败，再来一次。
        集合里只有真占着名额的（等着的不进去），面板上的占用数才准。"""
        while True:
            now = time.time()
            await asyncio.wait_for(self._client.zremrangebyscore(key, "-inf", now - self._lease), self._timeout)
            async with self._client.pipeline(transaction=True) as pipe:
                try:
                    await asyncio.wait_for(pipe.watch(key), self._timeout)
                    holding = await asyncio.wait_for(pipe.zcard(key), self._timeout)
                    if holding < limit:
                        pipe.multi()
                        pipe.zadd(key, {member: now})
                        pipe.expire(key, int(self._lease * 2))
                        await asyncio.wait_for(pipe.execute(), self._timeout)
                        return
                    await pipe.unwatch()
                except WatchError:
                    continue  # 别人同时拿或放了名额：马上再看一次
            await asyncio.sleep(self._poll)

    async def _refresh(self, key: str, member: str) -> None:
        """占着的时候每隔租期的三分之一续一次：只改自己的分数（XX），不会把已经失效的占位加回来。"""
        while True:
            await asyncio.sleep(self._lease / 3)
            try:
                await asyncio.wait_for(self._client.zadd(key, {member: time.time()}, xx=True), self._timeout)
            except (RedisError, TimeoutError, OSError):
                log.warning("refreshing gate %s failed", key, exc_info=True)

    def snapshot(self) -> dict[str, GateUse]:
        return self._tally.snapshot()


def build_gate(redis_url: str) -> tuple[Gate, Redis | None]:
    """配了 Redis 就用 Redis 版，否则进程内的。返回闸门和要在关停时关掉的客户端。"""
    if not redis_url:
        return LocalGate(), None
    client = Redis.from_url(redis_url, socket_timeout=OP_TIMEOUT, socket_connect_timeout=OP_TIMEOUT)
    return RedisGate(client), client
