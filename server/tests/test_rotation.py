"""模型令牌轮换：首次有限次重试、失败短退避、立即补发。"""

import asyncio

import pytest

from knowwork_server.gateway.credential_rotation import CredentialRotator

pytestmark = pytest.mark.anyio


class Host:
    def __init__(self, failures=0):
        self.failures = failures
        self.tokens: list[str] = []
        self.calls = 0

    async def push(self, token):
        self.calls += 1
        if self.failures > 0:
            self.failures -= 1
            raise ConnectionError("host down")
        self.tokens.append(token)


def issuer():
    counter = iter(range(1000))
    return lambda: f"token-{next(counter)}"


async def test_initial_rotation_retries_then_succeeds():
    host = Host(failures=2)
    rotator = CredentialRotator(host.push, issuer(), interval=60, retry_delays=(0.01,), initial_attempts=3)
    await rotator.start()
    assert host.calls == 3 and len(host.tokens) == 1
    await rotator.stop()


async def test_initial_rotation_gives_up_after_limit():
    host = Host(failures=10)
    rotator = CredentialRotator(host.push, issuer(), interval=60, retry_delays=(0.01,), initial_attempts=3)
    with pytest.raises(ConnectionError):
        await rotator.start()
    assert host.calls == 3


async def test_failed_rotation_retries_quickly_not_after_full_interval():
    host = Host()
    rotator = CredentialRotator(host.push, issuer(), interval=0.05, retry_delays=(0.01, 0.02))
    await rotator.start()
    host.failures = 2
    await asyncio.sleep(0.3)
    await rotator.stop()
    assert host.failures == 0 and len(host.tokens) >= 2


async def test_rotate_now_pushes_immediately_and_never_raises():
    host = Host()
    rotator = CredentialRotator(host.push, issuer(), interval=3600)
    await rotator.start()
    await rotator.rotate_now()
    host.failures = 1
    await rotator.rotate_now()
    assert host.tokens == ["token-0", "token-1"]
    await rotator.stop()


class Clock:
    """假的墙上时钟：advance 模拟电脑睡眠，期间 asyncio 的单调时钟不走。"""

    def __init__(self):
        self.now = 1_000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


async def test_ensure_fresh_rotates_only_when_the_wall_clock_says_so():
    host, clock = Host(), Clock()
    rotator = CredentialRotator(host.push, issuer(), interval=600, clock=clock)
    await rotator.start()
    await rotator.ensure_fresh()
    assert host.tokens == ["token-0"], "没到期不补发"
    clock.advance(1_800)  # 合盖半小时
    await rotator.ensure_fresh()
    await rotator.ensure_fresh()
    assert host.tokens == ["token-0", "token-1"]
    host.failures = 1
    clock.advance(600)
    await rotator.ensure_fresh()  # 失败只记日志，不抛
    await rotator.stop()


async def test_loop_rotates_soon_after_waking_not_a_full_interval_later():
    """单调时钟在睡眠时不走：循环按墙上时钟判断到期，醒来后一个小间隔内就轮换；墙上时钟没走就不轮换。"""
    host, clock = Host(), Clock()
    rotator = CredentialRotator(host.push, issuer(), interval=0.4, clock=clock)
    await rotator.start()
    await asyncio.sleep(0.3)
    assert host.tokens == ["token-0"], "墙上时钟没走，不到期"
    clock.advance(1_800)
    for _ in range(50):
        if len(host.tokens) == 2:
            break
        await asyncio.sleep(0.02)
    await rotator.stop()
    assert host.tokens == ["token-0", "token-1"]
