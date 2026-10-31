"""宿主内置的技能（Word、Excel、PPT）：业务服务自己不知道有哪些，经内核合同问宿主要一份名字与说明。

内置清单随宿主版本变，缓存几分钟；宿主暂时连不上就用上次的（第一次就连不上时是空的），不影响别的。
"""

import logging
import time
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from .views import AvailableSkill

log = logging.getLogger(__name__)

BUNDLED_TTL_SECONDS = 300
# 宿主连不上时隔这么久再问：不然每次打开 @ 菜单、技能页都要等一次超时
RETRY_SECONDS = 30
BundledFetch = Callable[[], Awaitable[Iterable[Any]]]


class BundledSkills:
    """宿主内置技能的缓存。fetch 返回带 name、description、user_invocable 的对象（内核合同的 BundledSkill）。"""

    def __init__(self, fetch: BundledFetch | None, *, ttl_seconds: float = BUNDLED_TTL_SECONDS,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._fetch = fetch
        self._ttl = ttl_seconds
        self._clock = clock
        self._cached: tuple[AvailableSkill, ...] = ()
        self._expires = float("-inf")

    async def list(self) -> tuple[AvailableSkill, ...]:
        if self._fetch is None or self._clock() < self._expires:
            return self._cached
        try:
            raw = await self._fetch()
            self._cached = tuple(AvailableSkill(name=str(item.name), description=str(item.description),
                                                source="bundled", version="",
                                                user_invocable=bool(item.user_invocable)) for item in raw)
            self._expires = self._clock() + self._ttl
        except Exception:  # 宿主连不上只是少列内置技能，不能拖垮技能页与 @ 菜单
            log.warning("bundled skills unavailable from host; listing without them", exc_info=True)
            self._expires = self._clock() + RETRY_SECONDS
        return self._cached
