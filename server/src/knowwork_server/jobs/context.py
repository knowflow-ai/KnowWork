"""处理函数拿到的执行上下文：载荷、分阶段记录、检查点、延长时限。

处理函数在每个阶段开始时（ctx.stage）和长循环里（ctx.checkpoint）响应取消与停机；等外部调用时，
运行器直接取消协程（取消标记、令牌丢失、停机宽限期到了），所以处理函数要写成可以在任何 await 处被打断。
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from .kinds import JobCanceled, JobError, JobKind, JobLost, JobReleased
from .store import Claim, JobStore

log = logging.getLogger(__name__)


class Stage:
    """一个阶段的输出摘要（页数、块数、耗时等），阶段结束时写进 job_stage.detail。"""

    def __init__(self, detail: dict) -> None:
        self.detail = detail

    def record(self, **values) -> None:
        self.detail = {**self.detail, **values}


class JobContext:
    def __init__(self, claim: Claim, kind: JobKind, store: JobStore) -> None:
        self.claim = claim
        self.kind = kind
        self._store = store
        self.canceled = False      # 看到了取消标记
        self.stopping = False      # worker 正在停机
        self.stop_reason: str | None = None  # 运行器硬取消协程前写上：canceled | lost | stopping
        self._deadline: asyncio.Timeout | None = None

    @property
    def workspace_id(self) -> str:
        return self.claim.workspace_id

    @property
    def subject_id(self) -> str:
        return self.claim.subject_id

    @property
    def payload(self) -> dict:
        return self.claim.payload

    @property
    def try_no(self) -> int:
        return self.claim.tries

    def checkpoint(self) -> None:
        """长循环里每做完一小步调一次：有取消就停，worker 停机就退回排队。"""
        if self.canceled and self.kind.cancelable:
            raise JobCanceled()
        if self.stopping:
            raise JobReleased()

    @asynccontextmanager
    async def stage(self, name: str, **detail) -> AsyncIterator[Stage]:
        self.checkpoint()
        opened = await self._store.open_stage(self.claim, self.kind.lease_seconds, name, detail)
        if opened is None:
            raise JobLost()
        stage_id, cancel_requested = opened
        self.canceled = self.canceled or cancel_requested
        if self.canceled and self.kind.cancelable:
            await self._store.close_stage(self.claim, stage_id, "canceled")
            raise JobCanceled()
        stage = Stage(detail)
        try:
            yield stage
        except (asyncio.CancelledError, JobCanceled, JobReleased, JobLost):
            raise  # 运行器收尾时统一关掉还开着的阶段
        except BaseException as error:
            await self._close_quietly(stage_id, "failed", _code(error), stage.detail)
            raise
        await self._close_quietly(stage_id, "done", None, stage.detail)

    async def _close_quietly(self, stage_id: str, status: str, code: str | None, detail: dict) -> None:
        try:
            await self._store.close_stage(self.claim, stage_id, status, error_code=code, detail=detail)
        except Exception:  # 阶段记录写不进去不影响任务本身，收尾时还会再关一次
            log.warning("failed to close stage %s of job %s", stage_id, self.claim.job_id, exc_info=True)

    def bind_deadline(self, deadline: asyncio.Timeout) -> None:
        self._deadline = deadline

    def extend_deadline(self, seconds: float) -> None:
        """把总时限延到「从现在起 seconds 秒」（只延长不缩短），例如拿到页数以后按页数放宽。"""
        if self._deadline is None:
            return
        when = asyncio.get_running_loop().time() + seconds
        current = self._deadline.when()
        if current is None or when > current:
            self._deadline.reschedule(when)

    def timed_out(self) -> bool:
        return self._deadline is not None and self._deadline.expired()


def _code(error: BaseException) -> str:
    return error.code if isinstance(error, JobError) else type(error).__name__[:64]
