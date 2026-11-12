"""网页嵌入的限次：固定窗口计数，防刷模型费用与数据库。

配了 Redis（KNOWWORK_REDIS_URL，部署时 api、worker 共用的那个）就记在 Redis，多个业务服务进程共用一份；没配时记在
进程内（本机开发、测试，单进程够用）。Redis 出错时放行并记日志：限次是防刷，不能因为它挂了让正常提问全都失败。
"""

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Protocol

from redis.asyncio import Redis

log = logging.getLogger(__name__)

OP_TIMEOUT = 1.0
PREFIX = "kw:limit:"
SEEN_PREFIX = "kw:once:"


class Limiter(Protocol):
    async def hit(self, key: str, limit: int, window: int) -> bool:
        """记一次；记上以后还在限额内返回真，超了返回假。window 是窗口秒数。"""
        ...

    async def once(self, key: str, ttl: int) -> bool:
        """第一次见到这个键返回真并记住 ttl 秒，之后再来返回假（认证访问的令牌只能用一次）。"""
        ...


class LocalLimiter:
    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._counts: dict[tuple[str, int, int], int] = {}
        self._seen: dict[str, float] = {}  # 键 → 到期时间

    async def once(self, key: str, ttl: int) -> bool:
        now = self._clock()
        self._seen = {name: until for name, until in self._seen.items() if until > now}
        if key in self._seen:
            return False
        self._seen[key] = now + ttl
        return True

    async def hit(self, key: str, limit: int, window: int) -> bool:
        slot = int(self._clock() // window)
        self._forget(slot, window)
        bucket = (key, window, slot)
        self._counts[bucket] = self._counts.get(bucket, 0) + 1
        return self._counts[bucket] <= limit

    def _forget(self, slot: int, window: int) -> None:
        stale = [bucket for bucket in self._counts if bucket[1] == window and bucket[2] < slot]
        for bucket in stale:
            del self._counts[bucket]


class RedisLimiter:
    def __init__(self, client: Redis, *, clock: Callable[[], float] = time.time) -> None:
        self._client = client
        self._clock = clock

    async def once(self, key: str, ttl: int) -> bool:
        """Redis 出错时不放行：令牌能不能重放是安全问题，不像限次那样宁可放过。"""
        try:
            fresh = await asyncio.wait_for(self._client.set(f"{SEEN_PREFIX}{key}", b"1", nx=True, ex=max(1, ttl)),
                                           OP_TIMEOUT)
        except Exception:
            log.warning("replay check for %s failed; refusing", key, exc_info=True)
            return False
        return bool(fresh)

    async def hit(self, key: str, limit: int, window: int) -> bool:
        slot = int(self._clock() // window)
        name = f"{PREFIX}{key}:{window}:{slot}"
        try:
            async with self._client.pipeline(transaction=True) as pipe:
                pipe.incr(name)
                pipe.expire(name, window * 2)
                count, _ = await asyncio.wait_for(pipe.execute(), OP_TIMEOUT)
        except Exception:
            log.warning("rate limit check for %s failed; letting it through", key, exc_info=True)
            return True
        return int(count) <= limit


def build_limiter(redis_url: str) -> tuple[Limiter, Redis | None]:
    """配了 Redis 就用 Redis 版，否则进程内的。返回限次器和要在关停时关掉的客户端。"""
    if not redis_url:
        return LocalLimiter(), None
    client = Redis.from_url(redis_url, socket_timeout=OP_TIMEOUT, socket_connect_timeout=OP_TIMEOUT)
    return RedisLimiter(client), client
