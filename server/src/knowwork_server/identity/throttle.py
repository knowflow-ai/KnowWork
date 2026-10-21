"""登录失败计数限流：按账号与按来源 IP 各计一份。

第一阶段是单进程内存实现；多副本部署时换成 Redis 等共享实现，只需满足 LoginThrottle 协议。
"""

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol


class LoginThrottle(Protocol):
    def blocked(self, keys: Sequence[str]) -> bool: ...

    def record_failure(self, keys: Sequence[str]) -> None: ...

    def reset(self, keys: Sequence[str]) -> None: ...


def account_key(email: str) -> str:
    return f"account:{email.strip().lower()[:320]}"


def ip_key(ip: str) -> str:
    return f"ip:{ip[:64]}"


@dataclass(frozen=True)
class Limits:
    window_seconds: float
    per_account: int
    per_ip: int

    def for_key(self, key: str) -> int:
        return self.per_account if key.startswith("account:") else self.per_ip


class InMemoryLoginThrottle:
    MAX_TRACKED_KEYS = 100_000

    def __init__(self, limits: Limits, clock: Callable[[], float] = time.monotonic) -> None:
        self._limits = limits
        self._clock = clock
        self._failures: dict[str, tuple[float, ...]] = {}

    def _recent(self, key: str, now: float) -> tuple[float, ...]:
        horizon = now - self._limits.window_seconds
        return tuple(stamp for stamp in self._failures.get(key, ()) if stamp > horizon)

    def blocked(self, keys: Sequence[str]) -> bool:
        now = self._clock()
        return any(len(self._recent(key, now)) >= self._limits.for_key(key) for key in keys)

    def record_failure(self, keys: Sequence[str]) -> None:
        now = self._clock()
        if len(self._failures) >= self.MAX_TRACKED_KEYS:
            self._prune(now)
        for key in keys:  # 限流器本身就是有状态的计数器，按键替换为新的元组
            self._failures[key] = (*self._recent(key, now), now)

    def reset(self, keys: Sequence[str]) -> None:
        for key in keys:
            self._failures.pop(key, None)

    def _prune(self, now: float) -> None:
        recent = {key: self._recent(key, now) for key in self._failures}
        self._failures = {key: stamps for key, stamps in recent.items() if stamps}
