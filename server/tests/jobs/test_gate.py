"""下游并发闸门：名额满了后来的要等；两个进程共用 Redis 里的名额；崩溃的占位过了租期自动失效；
出错也放掉名额；Redis 不可用时退回进程内；各进程的占用快照（心跳报给运行面板）；MinerU 的每次调用都过闸门。"""

import asyncio
import time

import fakeredis
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from knowwork_server.attachments.mineru import GatedMinerU
from knowwork_server.jobs.gate import PREFIX, GateUse, LocalGate, RedisGate

pytestmark = pytest.mark.anyio


async def in_redis(client) -> int:
    """Redis 里此刻占着名额的个数（所有进程合计）。"""
    return await client.zcard(PREFIX + "mineru")


async def contend(gate, limit, holders=3, hold_seconds=0.05):
    """几个人同时要名额，记下同时占着的最大人数。"""
    inside, peak = 0, 0

    async def one():
        nonlocal inside, peak
        async with gate.hold("mineru", limit):
            inside += 1
            peak = max(peak, inside)
            await asyncio.sleep(hold_seconds)
            inside -= 1

    await asyncio.gather(*(one() for _ in range(holders)))
    return peak


async def test_local_gate_limits_concurrency():
    gate = LocalGate()
    assert await contend(gate, 2, holders=5) == 2
    assert gate.snapshot() == {"mineru": GateUse(holding=0, limit=2, shared=False)}, "心跳报给面板：用完了、上限 2"


async def test_two_processes_share_the_redis_slots():
    server = fakeredis.FakeServer()
    client = fakeredis.FakeAsyncRedis(server=server)
    first = RedisGate(fakeredis.FakeAsyncRedis(server=server), poll_seconds=0.01)
    second = RedisGate(fakeredis.FakeAsyncRedis(server=server), poll_seconds=0.01)
    inside, peak = 0, 0

    async def one(gate):
        nonlocal inside, peak
        async with gate.hold("mineru", 2):
            inside += 1
            peak = max(peak, inside)
            assert await in_redis(client) <= 2
            assert sum(g.snapshot()["mineru"].holding for g in (first, second) if "mineru" in g.snapshot()) == inside
            await asyncio.sleep(0.05)
            inside -= 1

    await asyncio.gather(*(one(g) for g in (first, second, first, second, first)))
    assert peak == 2
    assert await in_redis(client) == 0, "用完都放掉了"
    assert first.snapshot()["mineru"] == GateUse(holding=0, limit=2, shared=True)


async def test_slots_of_a_crashed_worker_expire():
    client = fakeredis.FakeAsyncRedis()
    await client.zadd(PREFIX + "mineru", {"crashed-worker": time.time() - 10})  # 崩溃的 worker 留下的占位
    gate = RedisGate(client, lease_seconds=1.0, poll_seconds=0.01)
    async with asyncio.timeout(2):
        async with gate.hold("mineru", 1):
            assert await in_redis(client) == 1


async def test_slots_are_released_on_errors():
    client = fakeredis.FakeAsyncRedis()
    gate = RedisGate(client, poll_seconds=0.01)
    with pytest.raises(RuntimeError):
        async with gate.hold("mineru", 1):
            raise RuntimeError("解析崩了")
    assert await in_redis(client) == 0
    assert gate.snapshot()["mineru"].holding == 0
    async with asyncio.timeout(1), gate.hold("mineru", 1):
        pass


class BrokenRedis:
    """连不上的 Redis：所有操作都抛连接错误。"""

    async def zremrangebyscore(self, *args, **kwargs):
        raise RedisConnectionError("连不上")


async def test_falls_back_to_an_in_process_gate_when_redis_is_down():
    gate = RedisGate(BrokenRedis())
    assert await contend(gate, 1, holders=3) == 1, "退回进程内，名额照样管住"
    assert gate.snapshot()["mineru"] == GateUse(holding=0, limit=1, shared=False), "面板上标出没在共用"


class RecordingGate:
    def __init__(self):
        self.held = []

    def hold(self, name, limit):
        gate = self

        class Hold:
            async def __aenter__(self):
                gate.held.append((name, limit))

            async def __aexit__(self, *exc):
                return False

        return Hold()


class FakeClient:
    async def parse(self, filename, data, mime):
        return "# 正文"

    async def parse_full(self, filename, data, mime, *, layout=True, image_analysis=None):
        return "完整结果"


async def test_every_mineru_call_goes_through_the_gate():
    gate = RecordingGate()
    mineru = GatedMinerU(FakeClient(), gate, 3)
    assert await mineru.parse("a.pdf", b"x", "application/pdf") == "# 正文"
    assert await mineru.parse_full("a.pdf", b"x", "application/pdf") == "完整结果"
    assert gate.held == [("mineru", 3), ("mineru", 3)]
