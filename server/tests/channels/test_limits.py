"""网页嵌入的限次：固定窗口，超过限额就不放；换了窗口重新计；Redis 版多个进程共用一份计数，Redis 出错时放行。"""

import fakeredis
import pytest

from knowwork_server.channels.limits import LocalLimiter, RedisLimiter

pytestmark = pytest.mark.anyio


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


async def test_local_limiter_counts_within_a_window():
    clock = Clock()
    limiter = LocalLimiter(clock=clock)
    assert [await limiter.hit("v1", 3, 60) for _ in range(4)] == [True, True, True, False]
    assert await limiter.hit("v2", 3, 60), "别的键不受影响"
    clock.now += 60
    assert await limiter.hit("v1", 3, 60), "下一个窗口重新计"


async def test_local_limiter_forgets_old_windows():
    clock = Clock()
    limiter = LocalLimiter(clock=clock)
    for index in range(50):
        await limiter.hit(f"k{index}", 1, 60)
    clock.now += 600
    await limiter.hit("fresh", 1, 60)
    assert len(limiter._counts) == 1, "过了窗口的计数清掉，不会一直涨"


async def test_redis_limiter_is_shared_between_processes():
    server = fakeredis.FakeServer()
    clock = Clock()
    first = RedisLimiter(fakeredis.FakeAsyncRedis(server=server), clock=clock)
    second = RedisLimiter(fakeredis.FakeAsyncRedis(server=server), clock=clock)
    assert await first.hit("v1", 2, 60)
    assert await second.hit("v1", 2, 60)
    assert not await first.hit("v1", 2, 60), "两个进程合起来算"
    clock.now += 60
    assert await second.hit("v1", 2, 60)


async def test_redis_errors_let_requests_through():
    class Broken:
        def pipeline(self, *args, **kwargs):
            raise ConnectionError("redis down")

    limiter = RedisLimiter(Broken(), clock=Clock())
    assert await limiter.hit("v1", 1, 60)
    assert await limiter.hit("v1", 1, 60)


async def test_once_remembers_keys_until_they_expire():
    clock = Clock()
    for limiter in (LocalLimiter(clock=clock), RedisLimiter(fakeredis.FakeAsyncRedis(), clock=clock)):
        assert await limiter.once("jti-1", 60)
        assert not await limiter.once("jti-1", 60), "同一个令牌只能用一次"
        assert await limiter.once("jti-2", 60)
    local = LocalLimiter(clock=clock)
    assert await local.once("jti-3", 60)
    clock.now += 61
    assert await local.once("jti-3", 60), "过了有效期就忘了（令牌本身也早过期了）"


async def test_replay_checks_refuse_when_redis_is_down():
    class Broken:
        async def set(self, *args, **kwargs):
            raise ConnectionError("redis down")

    assert not await RedisLimiter(Broken(), clock=Clock()).once("jti", 60)
