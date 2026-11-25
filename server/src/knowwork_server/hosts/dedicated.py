"""个人专属宿主的管理：用到才建、空闲回收、数量有上限。几个业务服务进程可以同时用。

- 键是「空间 + 人」。这个人的对话要用上传的插件时建；建好以后把模型令牌、模型清单交给它（模型清单和共享宿主同一份；
  模型令牌是按这个人签的，带着归属，模型网关凭它按这个空间的内容安全策略再查一遍，见 assembly.py）；
- 「现在有哪些宿主」问启动器（容器起法问 Docker），不在本进程里另记一份：别的进程建的宿主这里照样找得到、接过来用
  （接过来时也给它现签模型令牌、同步模型清单），两个进程同时建同一个人的，后到的改去接先到的那个。本进程只缓存连宿主的客户端；
- 「在不在用」问宿主自己（GET /v1/activity）：离最后一次被对话用到多久、还有几段对话没真正停下来（正在跑、等审批、
  有订阅者、有后台任务）。几个进程看到的是同一份。没停下来的一律不收；
- 空闲超过时限就停掉（哪个进程的巡检先看到都行，重复停是空操作）。会话日志在这个人的持久目录里，下次再起接得上；
- 到数量上限时先收空闲最久的那个；都在用就拒绝，告诉用户稍后再试（不排队：排队的人不知道要等多久）；
- 这个人生效的插件变了：宿主空着就立刻换一个新的装上；正在用就先不动，等它空下来回收后再起时生效；
- 宿主自己退了（崩溃、被系统杀掉）：下次用到时重新起，巡检顺手清掉退出的容器；
- 业务服务进程退出时不停宿主（容器起法）：别的进程、重启后的自己接着用；
- 宿主回收后持久目录还留着；不在空间里的人、很久没用的人的持久目录由定期清理删掉（cleanup.py）。
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

import httpx

from ..kernel.contract import KernelError
from ..kernel.host_client import HostActivity, HostKernel
from .launcher import HostKey, HostLauncher, HostPlugin, Launched, LaunchFailed, LaunchRaced, signature_of

log = logging.getLogger(__name__)

Prepare = Callable[[HostKey, HostKernel], Awaitable[None]]
Cleanup = Callable[[], Awaitable[int]]
KernelFor = Callable[[Launched], HostKernel]
# 持久目录的清理：起来以后先等一会儿再跑第一次（不和启动时的活挤在一起），之后每 6 小时一次
CLEANUP_FIRST_SECONDS = 600.0
CLEANUP_EVERY_SECONDS = 6 * 3600.0
# 换插件、满了腾位置时宿主「空着」的标准：没有没停下的对话，且这么久没被用到
QUIET_SECONDS = 30.0


class HostUnavailable(KernelError):
    """专属宿主建不出来或满了；message 给用户看。"""

    def __init__(self, message: str) -> None:
        super().__init__("plugin_host_unavailable", message, 503)


@dataclass
class _Client:
    launched: Launched
    kernel: HostKernel


@dataclass(frozen=True)
class HostStatus:
    """运行面板用：一个专属宿主现在的样子。"""

    workspace_id: str
    user_id: str
    plugins: int
    sessions: int
    busy: int
    idle_seconds: float


def _kernel_for(launched: Launched) -> HostKernel:
    return HostKernel(launched.base_url, launched.token)


class DedicatedHosts:
    def __init__(self, launcher: HostLauncher, *, prepare: Prepare, max_hosts: int = 50, idle_seconds: float = 600.0,
                 kernel_for: KernelFor = _kernel_for) -> None:
        self._launcher = launcher
        self._prepare = prepare
        self._max = max_hosts
        self._idle = idle_seconds
        self._kernel_for = kernel_for
        self._clients: dict[HostKey, _Client] = {}
        self._locks: dict[HostKey, asyncio.Lock] = {}
        self._loops: list[asyncio.Task] = []

    def _lock(self, key: HostKey) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    # ---- 找、接、建 ----

    async def _client(self, key: HostKey) -> _Client | None:
        """这个人在跑的宿主的客户端：本进程连过的直接用；没连过的问启动器（可能是别的进程建的），接过来。"""
        cached = self._clients.get(key)
        if cached is not None and cached.launched.alive():
            return cached
        if cached is not None:
            await self._forget(key)
        try:
            launched = await self._launcher.find(key)
        except LaunchFailed:
            log.warning("plugin host %s/%s found but not usable", key.workspace_id, key.user_id, exc_info=True)
            return None
        if launched is None:
            return None
        return await self._attach(key, launched)

    async def _attach(self, key: HostKey, launched: Launched) -> _Client:
        """接过一个宿主：给它现签的模型令牌和模型清单（别的进程建的，令牌可能快过期了）。"""
        kernel = self._kernel_for(launched)
        try:
            await self._prepare(key, kernel)
        except Exception as error:
            await kernel.aclose()
            log.exception("plugin host %s/%s could not be prepared", key.workspace_id, key.user_id)
            raise HostUnavailable("插件的运行环境没有起来，请稍后再试；一直不行请联系管理员") from error
        client = _Client(launched, kernel)
        self._clients[key] = client
        return client

    async def _forget(self, key: HostKey) -> None:
        client = self._clients.pop(key, None)
        if client is not None:
            with contextlib.suppress(Exception):
                await client.kernel.aclose()

    async def lost(self, key: HostKey) -> None:
        """连这个人的宿主连不上了（被别的进程收了、自己退了）：放掉本进程的客户端，下次再找。"""
        await self._forget(key)

    async def kernel_of(self, key: HostKey) -> HostKernel | None:
        """这个人在跑的宿主；没有返回 None（调用方走恢复：重新打开时 ensure 会把它建起来）。"""
        client = await self._client(key)
        return client.kernel if client is not None else None

    async def ensure(self, key: HostKey, plugins: Sequence[HostPlugin]) -> HostKernel:
        """给这个人准备好宿主：没有就建；插件变了而宿主空着就换新的。"""
        signature = signature_of(plugins)
        async with self._lock(key):
            client = await self._client(key)
            if client is not None and client.launched.signature != signature and await self._quiet(client.kernel):
                log.info("plugin host %s/%s restarts with a new plugin set", key.workspace_id, key.user_id)
                await self._stop(key, client.launched)
                client = None
            if client is None:
                client = await self._create(key, plugins)
            return client.kernel

    async def _create(self, key: HostKey, plugins: Sequence[HostPlugin]) -> _Client:
        running = await self._launcher.running()
        if len(running) >= self._max and not await self._evict_one(running):
            raise HostUnavailable("插件的运行环境已经满了，请稍后再试")
        try:
            launched = await self._launcher.launch(key, plugins)
        except LaunchRaced:
            found = await self._launcher.find(key)  # 别的进程刚建了：接它的
            if found is None:
                raise HostUnavailable("插件的运行环境正在准备，请稍后再试") from None
            return await self._attach(key, found)
        except LaunchFailed as error:
            raise HostUnavailable("插件的运行环境没有起来，请稍后再试；一直不行请联系管理员") from error
        try:
            return await self._attach(key, launched)
        except HostUnavailable:
            await launched.stop()
            raise

    # ---- 在不在用 ----

    async def _activity(self, kernel: HostKernel) -> HostActivity | None:
        """问宿主；问不到（还没起好、刚被别的进程停掉）返回 None，当成在用，不收。"""
        try:
            return await kernel.activity()
        except (httpx.HTTPError, KernelError, ValueError):
            return None

    async def _idle_for(self, kernel: HostKernel) -> float | None:
        """空了多久；有没停下来的对话、或问不到时返回 None。"""
        activity = await self._activity(kernel)
        if activity is None or activity.busy > 0:
            return None
        return activity.idle_seconds

    async def _quiet(self, kernel: HostKernel) -> bool:
        idle = await self._idle_for(kernel)
        return idle is not None and idle >= QUIET_SECONDS

    async def _stop(self, key: HostKey, launched: Launched) -> None:
        await self._forget(key)
        try:
            await launched.stop()
        except Exception:
            log.exception("plugin host %s/%s did not stop cleanly", key.workspace_id, key.user_id)

    async def _evict_one(self, running: list[tuple[HostKey, Launched]]) -> bool:
        """满了：收掉空闲最久的那个（要空着）。都不空闲返回 False。"""
        idle: list[tuple[float, HostKey, Launched]] = []
        for key, launched in running:
            kernel = self._kernel_for(launched)
            try:
                seconds = await self._idle_for(kernel)
            finally:
                await kernel.aclose()
            if seconds is not None and seconds >= QUIET_SECONDS:
                idle.append((seconds, key, launched))
        if not idle:
            return False
        _, key, launched = max(idle, key=lambda item: item[0])
        await self._stop(key, launched)
        return True

    # ---- 回收 ----

    async def sweep(self) -> int:
        """收掉空闲超过时限的宿主（谁建的都算）。返回收了几个。"""
        stopped = 0
        for key, launched in await self._launcher.running():
            kernel = self._kernel_for(launched)
            try:
                idle = await self._idle_for(kernel)
            finally:
                await kernel.aclose()
            if idle is None or idle < self._idle:
                continue
            async with self._lock(key):
                await self._stop(key, launched)
            stopped += 1
        # 本进程连着、宿主已经不在了的客户端也放掉
        for key in [key for key, client in self._clients.items() if not client.launched.alive()]:
            await self._forget(key)
        return stopped

    async def remove_home(self, key: HostKey) -> bool:
        """删这个人的持久目录。他的宿主正在跑（谁建的都算）就不删，返回 False。"""
        async with self._lock(key):
            if await self._launcher.find(key) is not None:
                return False
            await self._launcher.remove_home(key)
            return True

    async def kernels(self) -> tuple[tuple[HostKey, HostKernel], ...]:
        """全部在跑的专属宿主和它们归谁（轮换模型令牌、同步模型清单时一起发，谁建的都算）。"""
        found = []
        for key, launched in await self._launcher.running():
            client = self._clients.get(key)
            if client is None or client.launched.base_url != launched.base_url:
                await self._forget(key)
                client = _Client(launched, self._kernel_for(launched))
                self._clients[key] = client
            found.append((key, client.kernel))
        return tuple(found)

    async def status(self) -> tuple[HostStatus, ...]:
        found = []
        for key, kernel in await self.kernels():
            activity = await self._activity(kernel)
            found.append(HostStatus(
                workspace_id=key.workspace_id, user_id=key.user_id, plugins=self._clients[key].launched.plugins,
                sessions=activity.sessions if activity else 0, busy=activity.busy if activity else 0,
                idle_seconds=round(activity.idle_seconds, 1) if activity else 0.0))
        return tuple(found)

    def start(self, interval_seconds: float = 60.0, *, cleanup: Cleanup | None = None) -> None:
        """定时回收空闲的宿主；给了 cleanup 时另外定时清理持久目录。"""
        if self._loops:
            return
        self._loops = [asyncio.create_task(_every(interval_seconds, interval_seconds, self.sweep, "plugin host sweep"))]
        if cleanup is not None:
            self._loops.append(asyncio.create_task(
                _every(CLEANUP_FIRST_SECONDS, CLEANUP_EVERY_SECONDS, cleanup, "plugin host home cleanup")))

    async def aclose(self) -> None:
        """停定时任务、放掉客户端。宿主本身交给启动器：容器起法留着给别的进程，进程起法停掉自己起的。"""
        for task in self._loops:
            task.cancel()
        await asyncio.gather(*self._loops, return_exceptions=True)
        self._loops = []
        for key in list(self._clients):
            await self._forget(key)
        await self._launcher.aclose()


async def _every(first: float, interval: float, job: Callable[[], Awaitable[int]], label: str) -> None:
    await asyncio.sleep(first)
    while True:
        try:
            await job()
        except Exception:
            log.exception("%s failed", label)
        await asyncio.sleep(interval)
