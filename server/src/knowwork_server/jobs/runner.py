"""任务执行器：按池领取、执行、按失败类别重试或收尾；续租、取消、超时、优雅停机。

- 池硬隔离：每个池固定几个执行槽，各自领自己池的任务，互不抢占，批量任务不会占满交互任务的容量；
- 执行槽没活时等 NOTIFY 唤醒，没有通知也每 idle_seconds 轮询一次兜底；
- 处理函数在单独的协程里跑，外面套总时限；旁边一个续租协程每 min(30 秒, 租约/3) 续一次，
  顺带发现取消标记和令牌丢失，直接取消处理协程；
- 处理函数抛出的任何异常都在这里接住、分类，不会让 worker 进程退出。
"""

import asyncio
import logging
import traceback
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import async_sessionmaker

from .context import JobContext
from .kinds import INTERNAL_DELAYS, JobCanceled, JobError, JobFailure, JobKind, JobLost, JobReleased
from .store import Claim, Done, GiveUp, JobStore, OnRetry, Settled

log = logging.getLogger(__name__)

Handler = Callable[[JobContext], Awaitable[Done | None]]
Workspaces = Callable[[], Iterable[str]]


@dataclass(frozen=True)
class Registration:
    kind: JobKind
    handler: Handler
    on_give_up: GiveUp | None = None
    on_retry: OnRetry | None = None


@dataclass(frozen=True)
class Outcome:
    status: str  # succeeded | retry | failed | canceled | released | superseded | lost
    error_code: str | None = None


@dataclass(frozen=True)
class _Active:
    ctx: JobContext
    task: asyncio.Task
    poke: asyncio.Event


def _now() -> datetime:
    return datetime.now(UTC)


class JobRunner:
    def __init__(self, sessionmaker: async_sessionmaker, *, pools: Mapping[str, int] | None = None,
                 clock: Callable[[], datetime] = _now) -> None:
        self._store = JobStore(sessionmaker, clock)
        self._pools = dict(pools or {"core": 2})
        self._registry: dict[str, Registration] = {}
        self._wake: dict[str, asyncio.Event] = {}
        self._running: dict[str, _Active] = {}
        self._stopping = False

    def register(self, kind: JobKind, handler: Handler, on_give_up: GiveUp | None = None,
                 on_retry: OnRetry | None = None) -> None:
        """on_give_up：最终失败时的业务收尾；on_retry：这次失败、排好了下次重试时调（业务据此显示「等待重试」
        和原因，不然对象一直停在「处理中」看不出失败过）。两个都在任务状态的同一个事务里执行。"""
        if kind.name in self._registry:
            raise ValueError(f"job kind already registered: {kind.name}")
        self._registry = {**self._registry, kind.name: Registration(kind, handler, on_give_up, on_retry)}

    @property
    def kinds(self) -> tuple[JobKind, ...]:
        """已注册的任务类型（运行面板、启动日志用）。"""
        return tuple(reg.kind for reg in self._registry.values())

    @property
    def pools(self) -> dict[str, int]:
        """各池的执行槽数：只算注册了任务类型的池，和 run_forever 起的执行槽一致。"""
        return {pool: size for pool, size in self._pools.items() if self._kinds(pool)}

    def busy(self) -> dict[str, int]:
        """各池此刻在跑几个（心跳报给运行面板）。"""
        return dict(Counter(active.ctx.kind.pool for active in self._running.values()))

    def _kinds(self, pool: str) -> dict[str, JobKind]:
        return {name: reg.kind for name, reg in self._registry.items() if reg.kind.pool == pool}

    async def _take(self, workspace_id: str, pool: str) -> Claim | Settled | None:
        kinds = self._kinds(pool)
        if not kinds:
            return None
        return await self._store.take(workspace_id, pool, kinds, lambda name: self._registry[name].on_give_up)

    async def claim(self, workspace_id: str, pool: str = "core") -> Claim | None:
        """只领取不执行（测试与排查用）；领取时就地收尾的任务跳过。"""
        while True:
            taken = await self._take(workspace_id, pool)
            if not isinstance(taken, Settled):
                return taken

    async def run_once(self, workspace_id: str, pool: str = "core") -> Outcome | None:
        """领取并执行一个任务；没有可执行的返回 None。"""
        taken = await self._take(workspace_id, pool)
        if taken is None:
            return None
        if isinstance(taken, Settled):
            return Outcome(taken.status, taken.error_code)
        return await self.execute(taken)

    async def execute(self, claim: Claim) -> Outcome:
        reg = self._registry[claim.kind]
        ctx = JobContext(claim, reg.kind, self._store)
        ctx.stopping = self._stopping
        task = asyncio.create_task(self._invoke(reg, ctx))
        active = _Active(ctx, task, asyncio.Event())
        self._running = {**self._running, claim.job_id: active}
        keeper = asyncio.create_task(self._keep_lease(active))
        try:
            await asyncio.wait({task})
        finally:
            keeper.cancel()
            self._running = {k: v for k, v in self._running.items() if k != claim.job_id}
            if not task.done():  # 执行器自己被取消（进程退出）：处理协程一起停，租约过期后由别的 worker 接手
                _interrupt(active, "stopping")
        return await self._settle(reg, ctx, task)

    async def _invoke(self, reg: Registration, ctx: JobContext) -> Done | None:
        async with asyncio.timeout(reg.kind.timeout_seconds) as deadline:
            ctx.bind_deadline(deadline)
            return await reg.handler(ctx)

    async def _keep_lease(self, active: _Active) -> None:
        ctx = active.ctx
        every = min(30.0, ctx.kind.lease_seconds / 3)
        while not active.task.done():
            await wait_any(active.poke, timeout=every)
            active.poke.clear()
            try:
                state = await self._store.renew(ctx.claim, ctx.kind.lease_seconds)
            except Exception:  # 数据库一时连不上：下一轮再续，租约还有余量
                log.warning("failed to renew lease of job %s", ctx.claim.job_id, exc_info=True)
                continue
            if state == "lost":
                _interrupt(active, "lost")
                return
            if state == "cancel" and ctx.kind.cancelable:
                ctx.canceled = True
                _interrupt(active, "canceled")
                return

    async def _settle(self, reg: Registration, ctx: JobContext, task: asyncio.Task) -> Outcome:
        try:
            return await self._settle_task(reg, ctx, task)
        except Exception:  # 收尾时数据库不可用：任务保持运行中，租约过期后被重新领取
            log.exception("failed to settle job %s", ctx.claim.job_id)
            return Outcome("lost", "settle_failed")

    async def _settle_task(self, reg: Registration, ctx: JobContext, task: asyncio.Task) -> Outcome:
        if task.cancelled():
            return await self._stopped(ctx, ctx.stop_reason or "stopping")
        error = task.exception()
        if error is None:
            return await self._succeed(reg, ctx, task.result())
        stops = {JobCanceled: "canceled", JobReleased: "stopping", JobLost: "lost"}
        for exc_type, reason in stops.items():
            if isinstance(error, exc_type):
                return await self._stopped(ctx, reason)
        if isinstance(error, TimeoutError) and ctx.timed_out():
            error = JobError("job_timeout", "处理超时", error_class="service")
        elif not isinstance(error, JobError):
            log.error("job %s (%s) crashed", ctx.claim.job_id, ctx.claim.kind, exc_info=error)
            error = JobError("internal_error", "处理出错", error_class="internal", detail=_trace(error))
        return await self._retry_or_fail(reg, ctx, error)

    async def _succeed(self, reg: Registration, ctx: JobContext, done: Done | None) -> Outcome:
        try:
            owned = await self._store.succeed(ctx.claim, done)
        except Exception as error:  # 业务收尾出错，整个事务已回滚，按内部错误重试
            log.exception("job %s finisher failed", ctx.claim.job_id)
            failure = JobError("finish_failed", "处理结果写入失败", error_class="internal", detail=_trace(error))
            return await self._retry_or_fail(reg, ctx, failure)
        if not owned:
            return _lost(ctx)
        return Outcome("succeeded", done.note if done else None)

    async def _retry_or_fail(self, reg: Registration, ctx: JobContext, error: JobError) -> Outcome:
        claim = ctx.claim
        # 运行面板不显示错误详情（可能带出文件内容），排查时按任务 id 在日志里找；内部错误的堆栈前面已经记过
        detail = "" if error.error_class == "internal" else error.detail or ""
        log.warning("job %s (%s) try %d failed: %s %s %s", claim.job_id, claim.kind, claim.tries, error.code,
                    error.message, detail)
        internal = error.error_class == "internal"
        delays = INTERNAL_DELAYS if internal else reg.kind.retry_delays
        limit = min(claim.max_tries, len(INTERNAL_DELAYS) + 1) if internal else claim.max_tries
        if error.retryable and claim.tries < limit:
            delay = error.retry_after
            if delay is None:
                delay = delays[min(claim.tries, len(delays)) - 1] if delays else 0.0
            return Outcome(await self._store.requeue(claim, delay, error, on_retry=reg.on_retry), error.code)
        failure = JobFailure(error.code, error.message, error.error_class, error.detail)
        if await self._store.fail(claim, failure, reg.on_give_up):
            return Outcome("failed", error.code)
        return _lost(ctx)

    async def _stopped(self, ctx: JobContext, reason: str) -> Outcome:
        if reason == "canceled":
            give_up = self._registry[ctx.claim.kind].on_give_up
            return Outcome("canceled") if await self._store.cancel_running(ctx.claim, give_up) else _lost(ctx)
        if reason == "stopping":
            status = await self._store.requeue(ctx.claim, 0.0, None, refund=True)
            return Outcome("released" if status == "retry" else status)
        return _lost(ctx)

    def on_notify(self, message: str) -> None:
        """LISTEN 收到的通知：pool:<池名> 有新任务，cancel:<任务 id> 有人要求取消。"""
        topic, _, value = message.partition(":")
        if topic == "pool":
            event = self._wake.pop(value, None)
            if event is not None:
                event.set()
        elif topic == "cancel" and value in self._running:
            self._running[value].poke.set()

    async def run_forever(self, workspaces: Workspaces, stop: asyncio.Event, *, idle_seconds: float = 5.0,
                          grace_seconds: float = 60.0, prune_seconds: float = 3600.0) -> None:
        """worker 进程的主循环，stop 置位后优雅停机：不再领取，手上的任务宽限期内到检查点就退回排队，
        宽限期过了直接取消并退回；进程被强杀的，由租约过期兜底。"""
        slots = [asyncio.create_task(self._slot(pool, workspaces, stop, idle_seconds), name=f"jobs:{pool}:{i}")
                 for pool, size in self._pools.items() if self._kinds(pool) for i in range(size)]
        pruner = asyncio.create_task(self._prune(workspaces, stop, prune_seconds))
        await stop.wait()
        await self._drain(slots, grace_seconds)
        pruner.cancel()
        await asyncio.gather(pruner, return_exceptions=True)

    async def _slot(self, pool: str, workspaces: Workspaces, stop: asyncio.Event, idle_seconds: float) -> None:
        while not stop.is_set():
            wake = self._wake.setdefault(pool, asyncio.Event())
            busy = False
            for workspace_id in _spaces(workspaces):
                if stop.is_set():
                    break
                try:
                    busy = await self.run_once(workspace_id, pool) is not None or busy
                except Exception:
                    log.exception("job slot %s failed in workspace %s", pool, workspace_id)
            if not busy:
                await wait_any(stop, wake, timeout=idle_seconds)

    async def _drain(self, slots: list[asyncio.Task], grace_seconds: float) -> None:
        self._stopping = True
        for active in self._running.values():
            active.ctx.stopping = True
        pending = set(slots)
        if pending:
            _, pending = await asyncio.wait(pending, timeout=grace_seconds)
        for active in list(self._running.values()):
            _interrupt(active, "stopping")
        if pending:
            _, stuck = await asyncio.wait(pending, timeout=10.0)
            for slot in stuck:
                slot.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    async def _prune(self, workspaces: Workspaces, stop: asyncio.Event, every: float) -> None:
        while not stop.is_set():
            for workspace_id in _spaces(workspaces):
                try:
                    await self._store.prune(workspace_id)
                except Exception:
                    log.warning("failed to prune jobs of workspace %s", workspace_id, exc_info=True)
            await wait_any(stop, timeout=every)


def _spaces(workspaces: Workspaces) -> list[str]:
    try:
        return list(workspaces())
    except Exception:  # 列工作空间失败（数据库一时不可用）：这一轮跳过，下一轮再来
        log.exception("failed to list workspaces for jobs")
        return []


def _interrupt(active: _Active, reason: str) -> None:
    active.ctx.stop_reason = active.ctx.stop_reason or reason
    active.task.cancel()


def _lost(ctx: JobContext) -> Outcome:
    log.warning("job %s was reclaimed by another worker before it finished", ctx.claim.job_id)
    return Outcome("lost")


def _trace(error: BaseException) -> str:
    return "".join(traceback.format_exception(error))


async def wait_any(*events: asyncio.Event, timeout: float | None) -> None:
    """等到任一事件置位或超时。"""
    waiters = [asyncio.ensure_future(event.wait()) for event in events]
    try:
        await asyncio.wait(waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for waiter in waiters:
            waiter.cancel()
