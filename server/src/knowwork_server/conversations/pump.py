"""事件泵：每个活跃会话一个后台任务，订阅宿主事件 → 分配序号并投影 → 推给浏览器 → 交给批量写入。

- 序号由泵在推送前分配；落库是批量异步的（writer.py），回放把库与未提交帧合并（replay.py）；
- 续读位置 (epoch, cursor) 记在内存里，泵首次启动时从库里最后一行恢复；宿主重启（epoch 变化）
  时从头读，并立即通知上层补发模型令牌；
- 空闲回收：收到 agent 空闲后再等一个短窗口，期间没有新帧也没有人再要求（ensure）就退出；
  agent 不在运行且长时间无帧时同样退出。下次发送或打开 SSE 时由服务层再 ensure；
- 任何异常都不让泵静默死掉：记日志、退避、从当前位置重读；坏帧由宿主客户端换成占位帧，游标照常推进；
- 浏览器断开不影响泵：任务的生命周期不绑在前端连接上；
- 思考给不给页面看按模型定（模型的「显示思考过程」，thinking_visible）：每次模型请求开始（request/header）按请求的模型问一次，
  不显示时这一步的思考片段不推、思考记录不进投影（原始帧照常落库，调试视图里还能看）。

限制：泵、广播与待写缓冲都在进程内，同一会话只能由一个业务服务进程承载（第一阶段单进程部署）。
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..db.base import enter_workspace
from ..kernel.contract import (
    GAP_FRAME_KIND,
    LIVE_FRAME_KIND,
    AgentKernel,
    EpochMismatch,
    KernelError,
    KernelEvent,
    SessionNotFound,
)
from ..models.usage import model_from_header
from .events_v1 import GAP_KIND, ProjectionState, project
from .listeners import Broadcaster, StoredEvent
from .live import LiveText, advance, snapshot
from .models import RunEvent
from .runlog import last_requested_model
from .sanitize import clean_json
from .writer import BatchWriter, PendingRow

log = logging.getLogger(__name__)

HostRestartHook = Callable[[], Awaitable[None]]


# 观察者：每条已落库的帧同步回调一次，必须立即返回（耗时操作自己放到后台），异常只记日志。
FrameObserver = Callable[[str, str, str, object], None]
# 模型别名 → 页面上要不要显示它的思考（models.ModelCatalog.shows_thinking）
ThinkingVisible = Callable[[str | None], Awaitable[bool]]


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
    live: LiveText | None = None  # 正在逐字输出的那一步（不落库，见 live.py）
    shows_thinking: bool = True  # 当前这次模型请求的思考给不给页面看（按请求的模型，见 thinking_visible）
    # 帧由外面送进来（会话在用户设备上执行，device.py）：不去宿主拉，送帧的请求按顺序一个一个处理
    pushed: bool = False
    push_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # 各来源已收到的最后一个游标（根会话、各个子 Agent 的会话各有各的序号）
    pushed_cursors: dict[str, int] = field(default_factory=dict)


class _Exit(Exception):
    """泵决定空闲退出。"""


class EventPump:
    def __init__(self, kernel: AgentKernel, sessionmaker: async_sessionmaker, writer: BatchWriter,
                 broadcaster: Broadcaster, *, on_host_restart: HostRestartHook | None = None,
                 timings: PumpTimings = DEFAULT_TIMINGS, observer: FrameObserver | None = None,
                 thinking_visible: ThinkingVisible | None = None) -> None:
        self._kernel = kernel
        self._thinking_visible = thinking_visible
        self._sessionmaker = sessionmaker
        self._writer = writer
        self._broadcaster = broadcaster
        self._on_host_restart = on_host_restart
        self._observer = observer
        self._t = timings
        self._streams: dict[str, _Stream] = {}

    def ensure(self, conversation_id: str, workspace_id: str) -> None:
        stream = self._streams.setdefault(conversation_id, _Stream(conversation_id, workspace_id))
        if stream.pushed:
            return  # 宿主上没有这段对话，拉不到东西
        stream.demand += 1
        if stream.active:
            return
        stream.active = True
        stream.task = asyncio.create_task(self._run(stream), name=f"event-pump:{conversation_id}")
        stream.task.add_done_callback(self._on_done)

    def is_active(self, conversation_id: str) -> bool:
        stream = self._streams.get(conversation_id)
        return stream is not None and stream.active

    def live_snapshot(self, conversation_id: str) -> dict | None:
        """正在逐字输出的那一步到目前为止的全文；浏览器中途打开、断线重连时先补这一份。"""
        stream = self._streams.get(conversation_id)
        return snapshot(stream.live) if stream is not None else None

    def produced_upto(self, conversation_id: str) -> int | None:
        """本进程已分配的最大序号；泵还没从库里恢复位置时返回 None。"""
        stream = self._streams.get(conversation_id)
        return stream.seq if stream is not None and stream.loaded else None

    async def push(self, conversation_id: str, workspace_id: str, frames: Sequence[KernelEvent]) -> int | None:
        """收下外面送来的帧（会话不在我们的宿主上跑，例如在用户设备上执行）：和拉来的帧走同一段——编号、投影、
        待写、推送、观察者（用量、引用这些照记）。帧的 epoch 是它的来源（根会话、某个子 Agent 的会话），各来源各有各的游标：
        同一来源里游标不大于已收到的跳过，所以重传不会存两份。返回这批帧的来源已收到的最后一个游标（没有帧时 None），
        送帧的一方从它之后接着送。"""
        async with self._pushed_stream(conversation_id, workspace_id) as stream:
            last = None
            for frame in frames:
                if frame.cursor > stream.pushed_cursors.get(frame.epoch, -1):
                    await self._accept(stream, frame)
                    stream.pushed_cursors[frame.epoch] = frame.cursor
                last = stream.pushed_cursors[frame.epoch]
            return last

    async def push_live(self, conversation_id: str, workspace_id: str, frames: Sequence[object]) -> None:
        """收下外面送来的逐字片段（和宿主发的临时帧同一种）：只推给正在看的页面，不落库、不占序号。"""
        async with self._pushed_stream(conversation_id, workspace_id) as stream:
            for data in frames:
                await self._accept(stream, KernelEvent(epoch=stream.epoch or "live", cursor=0, kind=LIVE_FRAME_KIND,
                                                       data=data))

    async def pushed_upto(self, conversation_id: str, workspace_id: str) -> dict[str, int]:
        """外面送帧的会话各来源已收到哪（业务服务重启过就从库里恢复）；没收到过的来源不在里面。"""
        async with self._pushed_stream(conversation_id, workspace_id) as stream:
            return dict(stream.pushed_cursors)

    @contextlib.asynccontextmanager
    async def _pushed_stream(self, conversation_id: str, workspace_id: str):
        """取这段对话的流并拿着它的锁：送帧的请求一个一个处理；第一次用先从库里恢复位置。"""
        stream = await self._pushed(conversation_id, workspace_id)
        async with stream.push_lock:
            enter_workspace(workspace_id)
            if not stream.loaded:
                await self._load(stream)
                await self._load_pushed(stream)
            yield stream

    async def _load_pushed(self, stream: _Stream) -> None:
        async with self._sessionmaker() as session:
            rows = (await session.execute(
                select(RunEvent.host_epoch, func.max(RunEvent.host_cursor))
                .where(RunEvent.conversation_id == stream.conversation_id).group_by(RunEvent.host_epoch))).all()
        stream.pushed_cursors = {epoch: cursor for epoch, cursor in rows}

    async def _pushed(self, conversation_id: str, workspace_id: str) -> _Stream:
        stream = self._streams.get(conversation_id)
        if stream is not None and stream.pushed:
            return stream
        if stream is not None:
            await self.stop(conversation_id)  # 先前有人当它在宿主上、拉过（不该发生）：停掉，别和送进来的帧抢序号
        stream = _Stream(conversation_id, workspace_id, pushed=True)
        self._streams[conversation_id] = stream
        return stream

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
        # 泵的任务可能由别的请求或宿主重启时拉起，不能沿用拉起方的空间：以会话所在的空间读写，观察者也跟着它
        enter_workspace(stream.workspace_id)
        try:
            await self._loop(stream)
        finally:
            if stream.task is me:
                stream.active = False

    async def _loop(self, stream: _Stream) -> None:
        attempt = 0
        while True:
            demand = stream.demand  # 这次读之前有多少人要过泵：读的过程中又有人要过，宿主说「会话不在」也再读一次
            try:
                if not stream.loaded:
                    await self._load(stream)
                received = await self._read(stream)
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
                # 宿主上没有这段对话（空闲释放了、还没打开）：泵不在这里等，打开它的一方（发消息、回答提问、到点激活）
                # 打开后会再 ensure。原来每 0.5 秒重试、一分钟没人要才退出，浏览器开着对话页时事件流每次重连都算有人要，
                # 就一直空转（实测一段对话十几个小时拉了五千多次 404）。
                # 读的过程中有人要过泵（ensure 看到泵还活着就没另起）才再读一次，否则当场退出：检查与退出之间没有 await，
                # ensure 要么在之前（计数变了、再读），要么在之后（看到泵已停、另起一个），不会两头落空
                if stream.demand == demand:
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
        if frame.kind == LIVE_FRAME_KIND:  # 临时帧：不分配序号、不落库、不推进续读位置
            if not stream.shows_thinking and isinstance(frame.data, dict) and frame.data.get("type") == "reasoning":
                return
            stream.live, payload = advance(stream.live, frame.data)
            if payload is not None:
                self._broadcaster.publish_live(stream.conversation_id, payload)
            return
        if stream.epoch is not None and frame.epoch != stream.epoch and not stream.pushed:
            await self._host_restarted()
        kind = frame.kind
        if kind == GAP_FRAME_KIND:
            log.warning("event pump %s: host buffer dropped frames %s", stream.conversation_id, frame.data)
            kind = GAP_KIND
        data = clean_json(frame.data)
        await self._follow_model(stream, kind, data)
        seq = stream.seq + 1
        projection, ui = project(stream.projection, kind, data, seq=seq, time=frame.time)
        if not stream.shows_thinking:
            ui = [event for event in ui if event.get("kind") != "thinking"]
        stored = StoredEvent(conversation_id=stream.conversation_id, seq=seq, kind=kind, data=data, ui=tuple(ui))
        await self._writer.put(PendingRow(event=stored, workspace_id=stream.workspace_id,
                                          host_epoch=frame.epoch, host_cursor=frame.cursor))
        # 从这里到推送之间没有 await：待写缓冲与推送对新登记的监听者是原子的。
        stream.seq, stream.projection = seq, projection
        stream.epoch, stream.cursor = frame.epoch, frame.cursor
        self._track_running(stream, kind, data)
        self._broadcaster.publish(stored)
        self._observe(stream, kind, data)

    async def _follow_model(self, stream: _Stream, kind: str, data: object) -> None:
        """每次模型请求开始时（request/header 带着请求的模型别名）问一次这个模型的思考给不给页面看。问不到按显示处理。"""
        if self._thinking_visible is None or kind != "session/event" or not isinstance(data, dict):
            return
        model = model_from_header(str(data.get("type")), data.get("data"))
        if model is not None:
            await self._apply_model(stream, model)

    async def _apply_model(self, stream: _Stream, model: str) -> None:
        try:
            stream.shows_thinking = await self._thinking_visible(model)
        except Exception:
            log.exception("thinking visibility lookup failed for %s; showing it", stream.conversation_id)
            stream.shows_thinking = True

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
            if not stream.running:
                stream.live = None  # 跑完了还没收到结束帧（取消、出错）：不再给新打开的页面补半截
        elif kind == "session/event" and isinstance(data, dict) and data.get("type") == "turn/start":
            stream.running = True
        elif stream.pushed and kind == "session/event" and isinstance(data, dict) and data.get("type") == "turn/end":
            # 外面送帧的会话没有宿主的状态帧：一轮结束就算停了，没收尾的半截逐字输出不再补给新打开的页面
            stream.running, stream.live = False, None

    async def _load(self, stream: _Stream) -> None:
        async with self._sessionmaker() as session:
            row = (await session.execute(
                select(RunEvent.seq, RunEvent.host_epoch, RunEvent.host_cursor)
                .where(RunEvent.conversation_id == stream.conversation_id)
                .order_by(RunEvent.seq.desc()).limit(1)
            )).first()
        if row is not None:
            stream.seq, stream.epoch, stream.cursor = row.seq, row.host_epoch, row.host_cursor
        if self._thinking_visible is not None:
            # request/header 只在第一次请求与换模型时有：泵重新拉起（业务服务重启、空闲回收后）从库里补上这段对话在用的模型
            model = await last_requested_model(self._sessionmaker, stream.conversation_id)
            if model is not None:
                await self._apply_model(stream, model)
        stream.loaded = True

    async def _host_restarted(self) -> None:
        if self._on_host_restart is None:
            return
        try:
            await self._on_host_restart()
        except Exception:
            log.exception("host restart hook failed")
