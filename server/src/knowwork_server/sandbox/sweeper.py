"""空闲清扫与孤儿回收。

Docker 没有空闲超时：容器会一直跑到有人停它。没有清扫，一个被丢下的会话就永远占着宿主机的内存与 CPU。
- 最后一次使用时间读容器里活动标记的 mtime（每次 exec 顺手 touch），没有就退回容器创建时间；
- 空闲超过容器创建时写在 label 上的 TTL（默认 1800 秒）就**删除**容器，不暂停：Docker 的 pause 让内存继续
  驻留在宿主机上，暂停一个没人要的沙箱什么也省不下；
- 给了 keep_stopped_seconds 时分两段（Office 编辑器用）：在跑的空闲超过 TTL 先**停掉**（释放内存，文件系统留着，
  再用时拉起来约 1 秒，新建要几秒到十秒），停着的空闲超过 TTL + keep_stopped_seconds 才删；
- 清扫在创建、连接时于后台触发，同一进程每 60 秒最多一次，单次最多跑 2 分钟；不是定时器；
- 删除前再判一次是否空闲（列表加逐个 stat 在繁忙 daemon 上要一会儿，期间会话可能又被用起来），
  并且持有该会话的锁、确认本进程没在用它。

清扫哪类容器（label）、容器归谁（key_of）、最后一次使用是什么时候（activity）都可以换：默认是会话沙箱这套，
扩展里的长驻容器（例如一人一个的 Office 编辑器）按自己的流量算活动，同一套清扫逻辑。
删之前还可以先做一件事（before_remove，例如 Office 编辑器把没存的文档存回对话）：持着锁跑，跑完再判一次空闲，
这期间又有人用了就不删；它失败不挡回收（自己记日志），免得一个坏容器永远收不掉。
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
    def lock(self, key: Any) -> asyncio.Lock: ...

    def is_busy(self, key: Any) -> bool: ...


def _timestamp(raw: Any) -> float | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed.timestamp() if parsed.year > 1 else None


# 容器列表里的 State：这几种算停着（没占内存，文件系统还在）
STOPPED_STATES = frozenset({"exited", "created"})


def _state(item: dict[str, Any]) -> str:
    return str(item.get("State") or "").lower()


def managed_filter() -> dict[str, str]:
    return {LABEL_COMPONENT: COMPONENT}


class IdleSweeper:
    def __init__(self, engine: DockerEngine, guard: Guard, *, default_ttl_seconds: int,
                 min_interval_seconds: float = 60.0, clock: Callable[[], float] = time.time,
                 labels: dict[str, str] | None = None,
                 key_of: Callable[[dict[str, str] | None], Any] = SandboxKey.from_labels,
                 activity: Callable[[dict[str, Any]], Awaitable[float | None]] | None = None,
                 before_remove: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
                 keep_stopped_seconds: int | None = None,
                 name: str = "sandbox-idle-sweep") -> None:
        self._engine = engine
        self._guard = guard
        self._ttl = default_ttl_seconds
        self._interval = min_interval_seconds
        self._clock = clock
        self._labels = labels if labels is not None else managed_filter()
        self._key_of = key_of
        self._activity = activity
        self._before_remove = before_remove
        self._keep_stopped = keep_stopped_seconds
        self._name = name
        self._last: float | None = None
        self._tasks: set[asyncio.Task] = set()

    def trigger(self) -> None:
        """后台跑一次清扫（距上次不到最小间隔就跳过）。失败只记日志：回收不能把一次正常操作变成错误。"""
        now = self._clock()
        if self._last is not None and now - self._last < self._interval:
            return
        self._last = now
        task = asyncio.create_task(self._run(), name=self._name)
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
        for item in await self._engine.list_containers(self._labels):
            # 分两段时，停着的按「TTL + 保留期」判，删之前没什么要存的（编辑器没在跑）
            stopped = self._keep_stopped is not None and _state(item) in STOPPED_STATES
            extra = self._keep_stopped if stopped and self._keep_stopped is not None else 0
            if not await self.is_idle(item, extra=extra):
                continue
            key = self._key_of(item.get("Labels"))
            if key is None:
                reclaimed += await self._remove(item)
                continue
            async with self._guard.lock(key):
                if self._guard.is_busy(key) or not await self.is_idle(item, extra=extra):
                    continue
                # 停着的到了保留期删；坏掉的（dead）停不了，空闲到时间直接删
                if stopped or _state(item) == "dead":
                    reclaimed += await self._remove(item)
                    continue
                if self._before_remove is not None and not await self._prepare(key, item):
                    continue
                reclaimed += await (self._stop(item) if self._keep_stopped is not None else self._remove(item))
        return reclaimed

    async def _stop(self, item: dict[str, Any]) -> int:
        try:
            await self._engine.stop_container(str(item["Id"]))
        except SandboxError:  # 一个停不掉不影响其他的，下一轮再试
            log.warning("空闲清扫：停止容器失败")
            return 0
        return 1

    async def _prepare(self, key: Any, item: dict[str, Any]) -> bool:
        """删之前的那件事；跑完还能删（没人在这期间用起来）才是 True。"""
        assert self._before_remove is not None
        try:
            await self._before_remove(item)
        except Exception:
            log.warning("回收前的处理失败，照样回收", exc_info=True)
        return not self._guard.is_busy(key) and await self.is_idle(item)

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
        if self._activity is not None:
            return await self._activity(item)
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

    async def is_idle(self, item: dict[str, Any], *, extra: int = 0) -> bool:
        """空闲超过 TTL（再加 extra 秒，停着的按保留期算）。"""
        ttl = self.ttl_for(item)
        if ttl <= 0:
            return False
        ttl += extra
        try:
            last = await self.last_activity(item)
        except SandboxError:
            return False
        # 既没有标记也没有创建时间：当成空闲可能删掉一个只是暂时读不到的沙箱
        return last is not None and self._clock() - last > ttl


async def reap_orphans(engine: DockerEngine, guard: Guard, is_live: LiveCheck, *, grace_seconds: float,
                       clock: Callable[[], float] = time.time) -> int:
    """删掉会话已经不存在的沙箱容器（默认没有调用方）。

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
