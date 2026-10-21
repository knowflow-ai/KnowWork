"""空闲清扫与孤儿回收（照抄 WeKnora 的 docker_idle_sweeper.go 与 orphan_reaper.go）。

Docker 没有空闲超时：容器会一直跑到有人停它。没有清扫，一个被丢下的会话就永远占着宿主机的内存与 CPU。
- 最后一次使用时间读容器里活动标记的 mtime（每次 exec 顺手 touch），没有就退回容器创建时间；
- 空闲超过容器创建时写在 label 上的 TTL（默认 1800 秒）就**删除**容器，不暂停：Docker 的 pause 让内存继续
  驻留在宿主机上，暂停一个没人要的沙箱什么也省不下；
- 清扫在创建、连接时于后台触发，同一进程每 60 秒最多一次，单次最多跑 2 分钟；不是定时器；
- 删除前再判一次是否空闲（列表加逐个 stat 在繁忙 daemon 上要一会儿，期间会话可能又被用起来），
  并且持有该会话的锁、确认本进程没在用它。
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Protocol

from .containers import COMPONENT, LABEL_COMPONENT, LABEL_IDLE_TTL, SandboxKey
from .contract import SandboxError
from .docker_engine import DockerEngine
from .scripts import ACTIVITY_MARKER

log = logging.getLogger(__name__)

SWEEP_BUDGET_SECONDS = 120.0
# daemon 主机与本机时钟允许的偏差：标记时间超出现在这么多，就当它是伪造的（脚本可以 touch -d 到未来，
# 那会让容器永远不被回收），退回创建时间。往过去改只会让它自己更早被回收。
CLOCK_SKEW_SECONDS = 120.0
LiveCheck = Callable[[str, str], Awaitable[bool]]


class Guard(Protocol):
    def lock(self, key: SandboxKey) -> asyncio.Lock: ...

    def is_busy(self, key: SandboxKey) -> bool: ...


def _timestamp(raw: Any) -> float | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed.timestamp() if parsed.year > 1 else None


def managed_filter() -> dict[str, str]:
    return {LABEL_COMPONENT: COMPONENT}


class IdleSweeper:
    def __init__(self, engine: DockerEngine, guard: Guard, *, default_ttl_seconds: int,
                 min_interval_seconds: float = 60.0, clock: Callable[[], float] = time.time) -> None:
        self._engine = engine
        self._guard = guard
        self._ttl = default_ttl_seconds
        self._interval = min_interval_seconds
        self._clock = clock
        self._last: float | None = None
        self._tasks: set[asyncio.Task] = set()

    def trigger(self) -> None:
        """后台跑一次清扫（距上次不到最小间隔就跳过）。失败只记日志：回收不能把一次正常操作变成错误。"""
        now = self._clock()
        if self._last is not None and now - self._last < self._interval:
            return
        self._last = now
        task = asyncio.create_task(self._run(), name="sandbox-idle-sweep")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self) -> None:
        try:
            reclaimed = await asyncio.wait_for(self.sweep(), SWEEP_BUDGET_SECONDS)
        except (SandboxError, TimeoutError):
            log.warning("沙箱空闲清扫失败", exc_info=True)
            return
        if reclaimed:
            log.info("沙箱空闲清扫回收了 %d 个容器", reclaimed)

    async def aclose(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def sweep(self) -> int:
        reclaimed = 0
        for item in await self._engine.list_containers(managed_filter()):
            if not await self.is_idle(item):
                continue
            key = SandboxKey.from_labels(item.get("Labels"))
            if key is None:
                reclaimed += await self._remove(item)
                continue
            async with self._guard.lock(key):
                if self._guard.is_busy(key) or not await self.is_idle(item):
                    continue
                reclaimed += await self._remove(item)
        return reclaimed

    async def _remove(self, item: dict[str, Any]) -> int:
        try:
            await self._engine.remove_container(str(item["Id"]))
        except SandboxError:  # 一个删不掉不影响其他的，下一轮再试
            log.warning("沙箱空闲清扫：删除容器失败")
            return 0
        return 1

    def ttl_for(self, item: dict[str, Any]) -> int:
        """优先用容器创建时写下的 TTL，不拿当前配置去套别的配置建的容器。"""
        raw = (item.get("Labels") or {}).get(LABEL_IDLE_TTL, "")
        return int(raw) if str(raw).isdigit() else self._ttl

    async def last_activity(self, item: dict[str, Any]) -> float | None:
        created = item.get("Created")
        fallback = float(created) if isinstance(created, int | float) and created > 0 else None
        stat = await self._engine.stat_path(str(item["Id"]), ACTIVITY_MARKER)
        marker = _timestamp((stat or {}).get("mtime"))
        if marker is None:
            return fallback
        if marker > self._clock() + CLOCK_SKEW_SECONDS:
            log.warning("沙箱活动标记的时间在未来，按创建时间计算空闲")
            return fallback
        return marker

    async def is_idle(self, item: dict[str, Any]) -> bool:
        ttl = self.ttl_for(item)
        if ttl <= 0:
            return False
        try:
            last = await self.last_activity(item)
        except SandboxError:
            return False
        # 既没有标记也没有创建时间：当成空闲可能删掉一个只是暂时读不到的沙箱
        return last is not None and self._clock() - last > ttl


async def reap_orphans(engine: DockerEngine, guard: Guard, is_live: LiveCheck, *, grace_seconds: float,
                       clock: Callable[[], float] = time.time) -> int:
    """删掉会话已经不存在的沙箱容器（照抄 WeKnora 的 ReapOrphanSandboxes；它在 WeKnora 里也没有默认调用方）。

    is_live(工作空间, 会话) 由主线提供（查会话表）。刚建不久（grace 内）的不动：会话行可能还没提交。
    查询失败就跳过这一个，不当成孤儿——拿不准时不删。
    """
    deleted = 0
    cutoff = clock() - grace_seconds
    for item in await engine.list_containers(managed_filter()):
        created = item.get("Created")
        if grace_seconds > 0 and isinstance(created, int | float) and created > cutoff:
            continue
        key = SandboxKey.from_labels(item.get("Labels"))
        if key is not None:
            try:
                if await is_live(key.workspace_id, key.session_id):
                    continue
            except Exception:
                log.warning("孤儿沙箱回收：查询会话失败，跳过", exc_info=True)
                continue
            if guard.is_busy(key):
                continue
        try:
            await engine.remove_container(str(item["Id"]))
        except SandboxError:
            continue
        deleted += 1
    return deleted
