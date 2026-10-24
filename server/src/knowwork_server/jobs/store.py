"""任务表的读写：领取、续租、阶段记录、收尾。每个方法一个短事务，外部调用（解析、模型）不在事务里做。

所有收尾都先锁住任务行并核对领取令牌：租约过期被别的 worker 接手以后，旧领取者写不进任何东西。
业务收尾（finisher、on_give_up）和任务状态在同一个事务里提交，「业务已标完成、任务还在跑」这种分叉不会出现。
"""

import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from ..access.rls import set_workspace
from .kinds import MAX_DETAIL, JobError, JobFailure, JobKind
from .models import CANCELED, FAILED, QUEUED, RUNNING, SUCCEEDED, Job, JobStage

log = logging.getLogger(__name__)

KEEP_DONE = timedelta(days=7)    # 成功、取消的任务行保留 7 天
KEEP_FAILED = timedelta(days=30)  # 失败的保留 30 天，运行面板可查、可重跑


@dataclass(frozen=True)
class Claim:
    job_id: str
    workspace_id: str
    kind: str
    subject_id: str
    payload: dict
    tries: int
    max_tries: int
    token: str


@dataclass(frozen=True)
class Done:
    """处理函数的返回值。apply 在「任务完成」的同一事务里执行（写业务结果）；
    note 是业务上的说明码（例如解析成功但放进沙箱失败），记进任务的 error_code 便于排查。"""

    apply: Callable[[AsyncSession], Awaitable[None]] | None = None
    note: str | None = None


GiveUp = Callable[[AsyncSession, Claim, JobFailure], Awaitable[None]]
# 这次失败、排好了下次重试：最后一个参数是下次什么时候试
OnRetry = Callable[[AsyncSession, Claim, JobFailure, datetime], Awaitable[None]]


@dataclass(frozen=True)
class Settled:
    """领取时就地收尾的任务（多次中断判失败、取消标记已在）：没有执行，但算干了活。"""

    job_id: str
    status: str
    error_code: str


# 运行面板上取消的：业务按失败收尾，给用户看的说明是这句
ADMIN_CANCELED = JobFailure("canceled_by_admin", "系统管理员取消了这次处理", "internal")


def _claim_of(job: Job) -> Claim:
    """领取时就地收尾的任务给失败收尾用的 Claim：没有令牌。"""
    return Claim(job.id, job.workspace_id, job.kind, job.subject_id, dict(job.payload or {}), job.tries,
                 job.max_tries, "")


def _clip(text: str | None) -> str | None:
    return text[-MAX_DETAIL:] if text else text


class JobStore:
    def __init__(self, sessionmaker: async_sessionmaker, clock: Callable[[], datetime]) -> None:
        self._sessionmaker = sessionmaker
        self._clock = clock

    @asynccontextmanager
    async def _tx(self, workspace_id: str) -> AsyncIterator[AsyncSession]:
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            yield session

    async def take(self, workspace_id: str, pool: str, kinds: Mapping[str, JobKind],
                   give_up: Callable[[str], GiveUp | None]) -> Claim | Settled | None:
        now = self._clock()
        async with self._tx(workspace_id) as session:
            job = await session.scalar(_ready(pool, kinds.values(), now))
            if job is None:
                return None
            kind = kinds[job.kind]
            if job.status == RUNNING:  # 租约过期：上一个 worker 崩溃或被杀
                await self._close_open_stages(session, job.id, "failed", "interrupted")
            # 排队中的被取消（只有运行面板会这样做）还没开始，什么任务类型都能停；运行中的要看能不能中途停
            if job.cancel_requested and (job.status == QUEUED or kind.cancelable):
                return await self._settle_canceled(session, job, give_up(job.kind))
            if job.status == RUNNING:
                settled = await self._settle_reclaimed(session, job, give_up)
                if settled is not None:
                    return settled
            # 先取值再更新：ORM 的 update 会把新值同步回会话里已加载的 job 对象
            claim = Claim(job_id=job.id, workspace_id=workspace_id, kind=job.kind, subject_id=job.subject_id,
                          payload=dict(job.payload or {}), tries=job.tries + 1, max_tries=job.max_tries,
                          token=uuid.uuid4().hex)
            await session.execute(update(Job).where(Job.id == job.id).values(
                status=RUNNING, tries=claim.tries, claim_token=claim.token, heartbeat_at=now, stage=None,
                lease_until=now + timedelta(seconds=kind.lease_seconds), updated_at=now))
            return claim

    async def _settle_canceled(self, session: AsyncSession, job: Job, give_up: GiveUp | None) -> Settled:
        await self._end(session, job.id, CANCELED, "canceled")
        if job.canceled_by:
            await _run_hook(session, give_up, _claim_of(job), ADMIN_CANCELED)
        return Settled(job.id, CANCELED, "canceled")

    async def _settle_reclaimed(self, session: AsyncSession, job: Job,
                                give_up: Callable[[str], GiveUp | None]) -> Settled | None:
        if job.tries < job.max_tries:
            return None
        last = job.heartbeat_at.isoformat(timespec="seconds") if job.heartbeat_at else "无"
        failure = JobFailure("interrupted", "处理多次中断，已停止", "internal",
                             detail=f"在「{job.stage or '开始'}」阶段中断 {job.tries} 次，最后心跳 {last}")
        await self._end(session, job.id, FAILED, failure.code, failure)
        await _run_hook(session, give_up(job.kind), _claim_of(job), failure)
        return Settled(job.id, FAILED, failure.code)

    async def renew(self, claim: Claim, lease_seconds: float) -> str:
        """续租并刷新心跳。返回 ok | cancel（有人要求取消）| lost（令牌已不是自己的）。"""
        now = self._clock()
        async with self._tx(claim.workspace_id) as session:
            flag = await session.scalar(update(Job).where(Job.id == claim.job_id, Job.claim_token == claim.token)
                                        .values(lease_until=now + timedelta(seconds=lease_seconds), heartbeat_at=now)
                                        .returning(Job.cancel_requested))
        if flag is None:
            return "lost"
        return "cancel" if flag else "ok"

    async def open_stage(self, claim: Claim, lease_seconds: float, name: str, detail: dict) -> tuple[str, bool] | None:
        """开始一个阶段：记阶段行、任务的当前阶段、心跳与续租。令牌已不是自己的返回 None。"""
        now = self._clock()
        stage_id = f"jst_{uuid.uuid4().hex}"
        async with self._tx(claim.workspace_id) as session:
            flag = await session.scalar(update(Job).where(Job.id == claim.job_id, Job.claim_token == claim.token)
                                        .values(stage=name, heartbeat_at=now, updated_at=now,
                                                lease_until=now + timedelta(seconds=lease_seconds))
                                        .returning(Job.cancel_requested))
            if flag is None:
                return None
            session.add(JobStage(id=stage_id, workspace_id=claim.workspace_id, job_id=claim.job_id,
                                 try_no=claim.tries, name=name, status="running", detail=detail, started_at=now))
        return stage_id, bool(flag)

    async def close_stage(self, claim: Claim, stage_id: str, status: str, *, error_code: str | None = None,
                          detail: dict | None = None) -> None:
        values = {"status": status, "error_code": error_code, "finished_at": self._clock()}
        if detail is not None:
            values["detail"] = detail
        async with self._tx(claim.workspace_id) as session:
            await session.execute(update(JobStage).where(JobStage.id == stage_id, JobStage.status == "running")
                                  .values(**values))

    async def succeed(self, claim: Claim, done: Done | None) -> bool:
        async with self._tx(claim.workspace_id) as session:
            if not await self._lock_own(session, claim):
                return False
            if done is not None and done.apply is not None:
                await done.apply(session)
            await self._close_open_stages(session, claim.job_id, "done")
            await self._end(session, claim.job_id, SUCCEEDED, done.note if done else None)
        return True

    async def requeue(self, claim: Claim, delay: float, error: JobError | None, *, refund: bool = False,
                      on_retry: OnRetry | None = None) -> str:
        """退回排队，返回 retry | superseded | lost。refund：停机释放，不算一次尝试。

        同一对象这期间又排进了一条新请求时（唯一索引冲突），这次重试被新请求取代，标 superseded，不再排。
        """
        now = self._clock()
        values = {"status": QUEUED, "next_attempt_at": now + timedelta(seconds=delay), "claim_token": None,
                  "lease_until": None, "stage": None, "updated_at": now,
                  "error_code": error.code if error else None, "error_class": error.error_class if error else None,
                  "error_detail": _clip(error.detail or error.message) if error else None}
        if refund:
            values["tries"] = claim.tries - 1
        async with self._tx(claim.workspace_id) as session:
            if not await self._lock_own(session, claim):
                return "lost"
            await self._close_open_stages(session, claim.job_id, "canceled" if refund else "failed",
                                          error.code if error else None)
            try:
                async with session.begin_nested():
                    await session.execute(update(Job).where(Job.id == claim.job_id).values(**values))
            except IntegrityError:
                await self._end(session, claim.job_id, CANCELED, "superseded")
                return "superseded"
            if error is not None and not refund and on_retry is not None:
                failure = JobFailure(error.code, error.message, error.error_class, error.detail)
                await _run_hook(session, lambda s, c, f: on_retry(s, c, f, values["next_attempt_at"]), claim, failure)
        return "retry"

    async def fail(self, claim: Claim, failure: JobFailure, give_up: GiveUp | None) -> bool:
        async with self._tx(claim.workspace_id) as session:
            if not await self._lock_own(session, claim):
                return False
            await self._close_open_stages(session, claim.job_id, "failed", failure.code)
            await self._end(session, claim.job_id, FAILED, failure.code, failure)
            await _run_hook(session, give_up, claim, failure)
        return True

    async def cancel_running(self, claim: Claim, give_up: GiveUp | None) -> bool:
        async with self._tx(claim.workspace_id) as session:
            if not await self._lock_own(session, claim):
                return False
            canceled_by = await session.scalar(select(Job.canceled_by).where(Job.id == claim.job_id))
            await self._close_open_stages(session, claim.job_id, "canceled")
            await self._end(session, claim.job_id, CANCELED, "canceled")
            if canceled_by:
                await _run_hook(session, give_up, claim, ADMIN_CANCELED)
        return True

    async def prune(self, workspace_id: str) -> None:
        now = self._clock()
        async with self._tx(workspace_id) as session:
            await session.execute(delete(Job).where(or_(
                and_(Job.status.in_((SUCCEEDED, CANCELED)), Job.finished_at < now - KEEP_DONE),
                and_(Job.status == FAILED, Job.finished_at < now - KEEP_FAILED))))

    async def _lock_own(self, session: AsyncSession, claim: Claim) -> bool:
        owned = await session.scalar(select(Job.id).where(Job.id == claim.job_id, Job.claim_token == claim.token)
                                     .with_for_update())
        return owned is not None

    async def _close_open_stages(self, session: AsyncSession, job_id: str, status: str,
                                 error_code: str | None = None) -> None:
        await session.execute(update(JobStage).where(JobStage.job_id == job_id, JobStage.status == "running")
                              .values(status=status, error_code=error_code, finished_at=self._clock()))

    async def _end(self, session: AsyncSession, job_id: str, status: str, code: str | None,
                   failure: JobFailure | None = None) -> None:
        now = self._clock()
        await session.execute(update(Job).where(Job.id == job_id).values(
            status=status, error_code=code, error_class=failure.error_class if failure else None,
            error_detail=_clip(failure.detail or failure.message) if failure else None, claim_token=None,
            lease_until=None, finished_at=now, updated_at=now))


def _ready(pool: str, kinds: Sequence[JobKind], now: datetime):
    """可领取的任务：到点的排队任务（同一对象没有同类任务在跑），或租约已过期的运行中任务。

    只领本 worker 注册过、且载荷版本不高于自己的任务类型；不认得的留在队列里，不判失败（滚动升级）。
    """
    sibling = aliased(Job)
    busy = select(sibling.id).where(sibling.workspace_id == Job.workspace_id, sibling.kind == Job.kind,
                                    sibling.subject_id == Job.subject_id, sibling.status == RUNNING).exists()
    known = or_(*(and_(Job.kind == k.name, Job.version <= k.version) for k in kinds))
    ready = or_(and_(Job.status == QUEUED, Job.next_attempt_at <= now, ~busy),
                and_(Job.status == RUNNING, Job.lease_until < now))
    return (select(Job).where(Job.pool == pool, known, ready)
            .order_by(Job.priority.desc(), Job.next_attempt_at, Job.id)
            .limit(1).with_for_update(skip_locked=True, of=Job))


async def _run_hook(session: AsyncSession, hook: GiveUp | None, claim: Claim, failure: JobFailure) -> None:
    """业务的失败收尾。它自己出错时只回滚它那一步，任务照样标失败，免得一条坏任务反复被领取。"""
    if hook is None:
        return
    try:
        async with session.begin_nested():
            await hook(session, claim, failure)
    except Exception:
        log.exception("job %s give-up hook failed", claim.job_id)
