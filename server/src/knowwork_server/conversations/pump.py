"""事件泵：每个活跃会话一个后台任务，订阅宿主事件 → 分配序号并投影 → 推给浏览器 → 交给批量写入。

- 序号由泵在推送前分配；落库是批量异步的（writer.py），回放把库与未提交帧合并（replay.py）；
- 续读位置 (epoch, cursor) 记在内存里，泵首次启动时从库里最后一行恢复；宿主重启（epoch 变化）
  时从头读，并立即通知上层补发模型令牌；
- 空闲回收：收到 agent 空闲后再等一个短窗口，期间没有新帧也没有人再要求（ensure）就退出；
  agent 不在运行且长时间无帧时同样退出。下次发送或打开 SSE 时由服务层再 ensure；
- 任何异常都不让泵静默死掉：记日志、退避、从当前位置重读；坏帧由宿主客户端换成占位帧，游标照常推进；
- 浏览器断开不影响泵：任务的生命周期不绑在前端连接上。

限制：泵、广播与待写缓冲都在进程内，同一会话只能由一个业务服务进程承载（第一阶段单进程部署）。
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..kernel.contract import GAP_FRAME_KIND, AgentKernel, EpochMismatch, KernelError, KernelEvent, SessionNotFound
from .events_v1 import GAP_KIND, ProjectionState, project
from .listeners import Broadcaster, StoredEvent
from .models import RunEvent
from .sanitize import clean_json
from .writer import BatchWriter, PendingRow

log = logging.getLogger(__name__)

HostRestartHook = Callable[[], Awaitable[None]]


# 观察者：每条已落库的帧同步回调一次，必须立即返回（耗时操作自己放到后台），异常只记日志。
FrameObserver = Callable[[str, str, str, object], None]


@dataclass(frozen=True)
class PumpTimings:
    idle_timeout: float = 60.0
    idle_grace: float = 3.0
    retry_delays: tuple[float, ...] = (0.5, 1.0, 2.0, 5.0, 10.0, 30.0)


DEFAULT_TIMINGS = PumpTimings()


@dataclass
class _Stream:
    """一个会话的泵状态。只在事件循环线程里读写。"""

    conversation_id: str
    workspace_id: str
    loaded: bool = False
    seq: int = 0
    epoch: str | None = None
    cursor: int = 0
    running: bool = False
    demand: int = 0
    active: bool = False
    task: asyncio.Task | None = None
    projection: ProjectionState = field(default_factory=ProjectionState)


class _Exit(Exception):
    """泵决定空闲退出。"""


class EventPump:
    def __init__(self, kernel: AgentKernel, sessionmaker: async_sessionmaker, writer: BatchWriter,
                 broadcaster: Broadcaster, *, on_host_restart: HostRestartHook | None = None,
                 timings: PumpTimings = DEFAULT_TIMINGS, observer: FrameObserver | None = None) -> None:
        self._kernel = kernel
        self._sessionmaker = sessionmaker
        self._writer = writer
        self._broadcaster = broadcaster
        self._on_host_restart = on_host_restart
        self._observer = observer
        self._t = timings
        self._streams: dict[str, _Stream] = {}

    def ensure(self, conversation_id: str, workspace_id: str) -> None:
        stream = self._streams.setdefault(conversation_id, _Stream(conversation_id, workspace_id))
        stream.demand += 1
        if stream.active:
            return
        stream.active = True
        stream.task = asyncio.create_task(self._run(stream), name=f"event-pump:{conversation_id}")
        stream.task.add_done_callback(self._on_done)

    def is_active(self, conversation_id: str) -> bool:
        stream = self._streams.get(conversation_id)
        return stream is not None and stream.active

    def produced_upto(self, conversation_id: str) -> int | None:
        """本进程已分配的最大序号；泵还没从库里恢复位置时返回 None。"""
        stream = self._streams.get(conversation_id)
        return stream.seq if stream is not None and stream.loaded else None

    async def stop(self, conversation_id: str) -> None:
        """停掉一个会话的泵并忘掉它（删除会话时用）。之后再 ensure 会从库里重新恢复位置。"""
        stream = self._streams.pop(conversation_id, None)
        if stream is None or stream.task is None or stream.task.done():
            return
        stream.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await stream.task

    async def close(self) -> None:
        tasks = [s.task for s in self._streams.values() if s.task is not None and not s.task.done()]
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task

    @staticmethod
    def _on_done(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            log.error("event pump %s died", task.get_name(), exc_info=error)

    async def _run(self, stream: _Stream) -> None:
        me = asyncio.current_task()
        try:
            await self._loop(stream)
        finally:
            if stream.task is me:
                stream.active = False

    async def _loop(self, stream: _Stream) -> None:
        attempt = 0
        missing_since: int | None = None  # 会话在宿主侧不存在时，记下当时的 demand
        while True:
            try:
                if not stream.loaded:
                    await self._load(stream)
                received = await self._read(stream)
                missing_since = None
                # 宿主正常结束了订阅：有帧就马上续读，连续空读则逐步退避
                attempt = 0 if received else attempt + 1
                delay = self._t.retry_delays[0] if received else self._backoff(attempt)
            except _Exit:
                return
            except asyncio.CancelledError:
                raise
            except EpochMismatch:
                stream.epoch, stream.cursor = None, 0
                await self._host_restarted()
                continue
            except SessionNotFound:
                # 会话由发送方负责恢复；长时间没人要求就退出，不在这里空转
                missing_since = stream.demand if missing_since is None else missing_since
                attempt += 1
                if attempt * self._t.retry_delays[0] >= self._t.idle_timeout and stream.demand == missing_since:
                    stream.active = False
                    return
                delay = self._t.retry_delays[0]
            except (KernelError, httpx.HTTPError, OSError) as error:
                delay = self._backoff(attempt)
                attempt += 1
                log.warning("event pump %s: host unavailable (%s), retry in %.1fs",
                            stream.conversation_id, type(error).__name__, delay)
            except Exception:
                delay = self._backoff(attempt)
                attempt += 1
                log.exception("event pump %s failed, retry in %.1fs", stream.conversation_id, delay)
            await asyncio.sleep(delay)

    def _backoff(self, attempt: int) -> float:
        return self._t.retry_delays[min(attempt, len(self._t.retry_delays) - 1)]

    async def _read(self, stream: _Stream) -> int:
        """读一次订阅，返回收到的帧数。空闲退出时抛 _Exit（并已在同一循环片里标记非活跃）。"""
        loop = asyncio.get_running_loop()
        mark, received = stream.demand, 0
        try:
            async with asyncio.timeout(None if stream.running else self._t.idle_timeout) as timer:
                async for frame in self._kernel.subscribe(stream.conversation_id, after=stream.cursor,
                                                          epoch=stream.epoch):
                    await self._accept(stream, frame)
                    received += 1
                    mark = stream.demand
                    if self._is_idle(frame):
                        timer.reschedule(loop.time() + self._t.idle_grace)
                    else:
                        timer.reschedule(None if stream.running else loop.time() + self._t.idle_timeout)
        except TimeoutError:
            if stream.demand == mark and not stream.running:
                stream.active = False
                raise _Exit from None
            # 等待期间有人再次要求，或 agent 又跑起来了：重新订阅续读
            return max(received, 1)
        return received

    @staticmethod
    def _is_idle(frame: KernelEvent) -> bool:
        return frame.kind == "agent/status" and isinstance(frame.data, dict) and frame.data.get("status") == "idle"

    async def _accept(self, stream: _Stream, frame: KernelEvent) -> None:
        if stream.epoch is not None and frame.epoch != stream.epoch:
            await self._host_restarted()
        kind = frame.kind
        if kind == GAP_FRAME_KIND:
            log.warning("event pump %s: host buffer dropped frames %s", stream.conversation_id, frame.data)
            kind = GAP_KIND
        data = clean_json(frame.data)
        seq = stream.seq + 1
        projection, ui = project(stream.projection, kind, data, seq=seq, time=frame.time)
        stored = StoredEvent(conversation_id=stream.conversation_id, seq=seq, kind=kind, data=data, ui=tuple(ui))
        await self._writer.put(PendingRow(event=stored, workspace_id=stream.workspace_id,
                                          host_epoch=frame.epoch, host_cursor=frame.cursor))
        # 从这里到推送之间没有 await：待写缓冲与推送对新登记的监听者是原子的。
        stream.seq, stream.projection = seq, projection
        stream.epoch, stream.cursor = frame.epoch, frame.cursor
        self._track_running(stream, kind, data)
        self._broadcaster.publish(stored)
        self._observe(stream, kind, data)

    def _observe(self, stream: _Stream, kind: str, data: object) -> None:
        if self._observer is None:
            return
        try:
            self._observer(stream.conversation_id, stream.workspace_id, kind, data)
        except Exception:
            log.exception("event observer failed on %s", stream.conversation_id)

    @staticmethod
    def _track_running(stream: _Stream, kind: str, data: object) -> None:
        if kind == "agent/status" and isinstance(data, dict):
            stream.running = data.get("status") != "idle"
        elif kind == "session/event" and isinstance(data, dict) and data.get("type") == "turn/start":
            stream.running = True

    async def _load(self, stream: _Stream) -> None:
        async with self._sessionmaker() as session:
            row = (await session.execute(
                select(RunEvent.seq, RunEvent.host_epoch, RunEvent.host_cursor)
                .where(RunEvent.conversation_id == stream.conversation_id)
                .order_by(RunEvent.seq.desc()).limit(1)
            )).first()
        if row is not None:
            stream.seq, stream.epoch, stream.cursor = row.seq, row.host_epoch, row.host_cursor
        stream.loaded = True

    async def _host_restarted(self) -> None:
        if self._on_host_restart is None:
            return
        try:
            await self._on_host_restart()
        except Exception:
            log.exception("host restart hook failed")
