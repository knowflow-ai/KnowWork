"""模型网关短期令牌的轮换。

- 启动时必须先成功下发一次（有限次重试），否则服务不启动：宿主没有令牌就调不了模型；
- 之后按 TTL 的三分之一定期轮换；失败按 5 → 10 → 30 秒短退避重试，旧令牌在有效期内仍可用；
- 宿主重启后宿主侧凭据可能已丢，调用 rotate_now() 立即补发（事件泵发现 epoch 变化、发送走恢复时）；
- 间隔按墙上时钟算，循环只是每隔一小段（TICK_SECONDS）看一眼到没到期。asyncio 的计时用单调时钟，
  macOS 睡眠时它不走，而令牌的有效期是墙上时间：合盖半小时醒来，令牌早过期了，按单调时钟却还要再等几分钟
  才轮换，这期间每一轮都因模型网关 401 失败（2026-10-20 实测）。每轮开始前另调 ensure_fresh() 兜底。
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable

log = logging.getLogger(__name__)

RETRY_DELAYS = (5.0, 10.0, 30.0)
INITIAL_ATTEMPTS = 4
TICK_SECONDS = 15.0


class CredentialRotator:
    def __init__(self, push: Callable[[str], Awaitable[None]], issue: Callable[[], str], *, interval: float,
                 retry_delays: tuple[float, ...] = RETRY_DELAYS, initial_attempts: int = INITIAL_ATTEMPTS,
                 clock: Callable[[], float] = time.time) -> None:
        self._push = push
        self._issue = issue
        self._interval = interval
        self._tick = min(TICK_SECONDS, interval / 4)
        self._retry_delays = retry_delays
        self._initial_attempts = initial_attempts
        self._clock = clock
        self._rotated_at: float | None = None  # 墙上时钟：上次下发成功的时间
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        for attempt in range(self._initial_attempts):
            try:
                await self._rotate()
                break
            except Exception:
                if attempt == self._initial_attempts - 1:
                    raise
                delay = self._delay(attempt)
                log.warning("initial model token rotation failed, retry in %.1fs", delay, exc_info=True)
                await asyncio.sleep(delay)
        self._task = asyncio.create_task(self._forever(), name="model-token-rotation")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def rotate_now(self) -> None:
        """立即补发一次；失败只记日志，由定期轮换继续兜底。"""
        try:
            await self._rotate()
        except Exception:
            log.exception("immediate model token rotation failed")

    async def ensure_fresh(self) -> None:
        """每轮开始前调用：距上次下发已满一个间隔（例如电脑刚从睡眠醒来）就立即补发。失败只记日志。"""
        try:
            await self._rotate(only_if_due=True)
        except Exception:
            log.exception("model token refresh before turn failed")

    def _due(self) -> bool:
        return self._rotated_at is None or self._clock() - self._rotated_at >= self._interval

    async def _rotate(self, *, only_if_due: bool = False) -> None:
        async with self._lock:
            if only_if_due and not self._due():
                return  # 排队等锁时别人已经补发过了
            await self._push(self._issue())
            self._rotated_at = self._clock()

    def _delay(self, attempt: int) -> float:
        return self._retry_delays[min(attempt, len(self._retry_delays) - 1)]

    async def _forever(self) -> None:
        failures = 0
        while True:
            await asyncio.sleep(self._tick if failures == 0 else self._delay(failures - 1))
            if failures == 0 and not self._due():
                continue
            try:
                await self._rotate()
                failures = 0
            except Exception:
                failures += 1
                log.exception("model token rotation failed (%d in a row)", failures)
