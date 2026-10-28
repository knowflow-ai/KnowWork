"""运行面板（系统管理员）：各池的排队、重试、运行、失败数，最老排队任务等了多久，最近 24 小时
处理与失败数；按状态翻看任务；对单个任务取消、立即重跑、删除失败记录；worker 心跳与下游闸门占用。

- 只给看 id、类型、对象 id、阶段、错误码与阶段计数，不给载荷与错误详情（里面可能有文件内容、内部地址）；
  错误详情在 worker 日志里；
- 取消：排队中的、运行中的都只打标记（canceled_by 记下是谁），由 worker 停下并走业务的失败收尾，
  业务对象（文档、附件）才不会一直停在「处理中」。做到一半停下会留下半截状态的类型（删除、导出导入）运行中不让取消；
- 立即重跑：排队中的（在等重试退避）马上可领；失败、取消的当成一次新请求重新排队（尝试次数清零，旧的阶段记录清掉）。
  处理函数开头都核对业务状态（轮次、删除中），业务已经往前走了的，重跑时会自己停手；
- 开源版只有默认空间，任务表开了行级安全，这里按默认空间读写；多空间时按空间汇总。
"""

import base64
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..access.rls import set_workspace
from .heartbeat import HEARTBEAT_STALE
from .models import CANCELED, FAILED, QUEUED, RUNNING, SUCCEEDED, Job, JobStage, JobWorker
from .queue import notify

PAGE = 50
RECENT = timedelta(hours=24)
POOL_ORDER = ("core", "enrichment", "maintenance")
RETRYING = "retrying"  # 列表筛选用：排队中、已经试过至少一次（在等重试退避）
STATUSES = (QUEUED, RETRYING, RUNNING, SUCCEEDED, FAILED, CANCELED)


class JobNotFound(Exception):
    pass


class JobConflict(Exception):
    """任务当前的状态不允许这个操作，message 是给管理员看的中文。"""


@dataclass(frozen=True)
class PoolStats:
    pool: str
    slots: int            # 在线 worker 的执行槽合计
    queued: int           # 排队中、还没试过
    retrying: int         # 排队中、在等重试
    running: int
    failed: int           # 保留期内的失败记录（30 天）
    oldest_wait_seconds: float | None  # 到点了还没被领走的任务里等得最久的
    succeeded_recent: int  # 最近 24 小时
    failed_recent: int


@dataclass(frozen=True)
class KindInfo:
    name: str
    label: str
    pool: str
    cancelable: bool


@dataclass(frozen=True)
class WorkerInfo:
    id: str
    host: str
    pid: int
    version: str
    pools: dict[str, int]
    busy: dict[str, int]
    alive: bool
    started_at: datetime
    heartbeat_at: datetime


@dataclass(frozen=True)
class GateInfo:
    name: str
    holding: int  # 在线 worker 合计
    limit: int
    shared: bool  # 经 Redis 共用：limit 是全部 worker 共用的上限；否则每个 worker 各自这么多


@dataclass(frozen=True)
class Overview:
    pools: tuple[PoolStats, ...]
    workers: tuple[WorkerInfo, ...]
    gates: tuple[GateInfo, ...]
    kinds: tuple[KindInfo, ...]


@dataclass(frozen=True)
class JobRow:
    id: str
    kind: str
    subject_id: str
    pool: str
    status: str
    stage: str | None
    tries: int
    max_tries: int
    error_code: str | None
    error_class: str | None
    cancel_requested: bool
    created_at: datetime
    updated_at: datetime
    next_attempt_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True)
class StageRow:
    try_no: int
    name: str
    status: str
    error_code: str | None
    detail: dict
    started_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True)
class JobPage:
    items: tuple[JobRow, ...]
    next: str | None


@dataclass(frozen=True)
class JobFilter:
    status: str | None = None
    pool: str | None = None
    kind: str | None = None


def _now() -> datetime:
    return datetime.now(UTC)


def _row(job: Job) -> JobRow:
    return JobRow(id=job.id, kind=job.kind, subject_id=job.subject_id, pool=job.pool, status=job.status,
                  stage=job.stage, tries=job.tries, max_tries=job.max_tries, error_code=job.error_code,
                  error_class=job.error_class, cancel_requested=job.cancel_requested, created_at=job.created_at,
                  updated_at=job.updated_at, next_attempt_at=job.next_attempt_at, finished_at=job.finished_at)


def _encode(created_at: datetime, job_id: str) -> str:
    return base64.urlsafe_b64encode(json.dumps([created_at.isoformat(), job_id]).encode()).decode()


def _decode(cursor: str) -> tuple[datetime, str]:
    try:
        created, job_id = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        return datetime.fromisoformat(created), str(job_id)
    except (ValueError, TypeError) as error:
        raise JobConflict("翻页位置无效，请从第一页重新看") from error


def _pool_key(pool: str) -> tuple[int, str]:
    return (POOL_ORDER.index(pool) if pool in POOL_ORDER else len(POOL_ORDER), pool)


class JobsAdmin:
    def __init__(self, sessionmaker: async_sessionmaker, *, clock: Callable[[], datetime] = _now) -> None:
        self._sessionmaker = sessionmaker
        self._clock = clock

    @asynccontextmanager
    async def _tx(self, workspace_id: str) -> AsyncIterator[AsyncSession]:
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            yield session

    async def overview(self, workspace_id: str) -> Overview:
        now = self._clock()
        async with self._tx(workspace_id) as session:
            counts = (await session.execute(_pool_counts(now))).all()
            workers = (await session.scalars(select(JobWorker).order_by(JobWorker.started_at))).all()
        infos = tuple(_worker(w, now) for w in workers)
        alive = [w for w in workers if _alive(w, now)]
        slots: dict[str, int] = {}
        for worker in alive:
            for pool, size in (worker.pools or {}).items():
                slots = {**slots, pool: slots.get(pool, 0) + int(size)}
        stats = {row.pool: row for row in counts}
        pools = tuple(PoolStats(
            pool=pool, slots=slots.get(pool, 0), queued=_n(stats, pool, "queued"), retrying=_n(stats, pool, "retrying"),
            running=_n(stats, pool, "running"), failed=_n(stats, pool, "failed"),
            oldest_wait_seconds=_wait(stats.get(pool), now), succeeded_recent=_n(stats, pool, "succeeded_recent"),
            failed_recent=_n(stats, pool, "failed_recent"))
            for pool in sorted({*stats, *slots}, key=_pool_key))
        return Overview(pools=pools, workers=infos, gates=_gates(alive), kinds=_kinds(workers))

    async def jobs(self, workspace_id: str, where: JobFilter, *, cursor: str | None = None, limit: int = PAGE) -> JobPage:
        query = select(Job).where(*_conditions(where)).order_by(Job.created_at.desc(), Job.id.desc()).limit(limit + 1)
        if cursor:
            created, job_id = _decode(cursor)
            query = query.where(or_(Job.created_at < created, and_(Job.created_at == created, Job.id < job_id)))
        async with self._tx(workspace_id) as session:
            rows = (await session.scalars(query)).all()
        items = tuple(_row(job) for job in rows[:limit])
        more = len(rows) > limit
        return JobPage(items=items, next=_encode(rows[limit - 1].created_at, rows[limit - 1].id) if more else None)

    async def detail(self, workspace_id: str, job_id: str) -> tuple[JobRow, tuple[StageRow, ...]]:
        async with self._tx(workspace_id) as session:
            job = await self._get(session, job_id)
            stages = (await session.scalars(select(JobStage).where(JobStage.job_id == job_id)
                                            .order_by(JobStage.started_at, JobStage.id))).all()
        return _row(job), tuple(StageRow(try_no=s.try_no, name=s.name, status=s.status, error_code=s.error_code,
                                         detail=dict(s.detail or {}), started_at=s.started_at,
                                         finished_at=s.finished_at) for s in stages)

    async def cancel(self, workspace_id: str, actor: str, job_id: str) -> JobRow:
        now = self._clock()
        async with self._tx(workspace_id) as session:
            job = await self._get(session, job_id, lock=True)
            if job.status not in (QUEUED, RUNNING):
                raise JobConflict("任务已经结束，不用取消")
            if job.status == RUNNING and not await _cancelable(session, job.kind):
                raise JobConflict("这类任务做到一半停下会留下半截状态，运行中不能取消，等它做完或失败")
            job.cancel_requested, job.canceled_by, job.updated_at = True, actor, now
            if job.status == QUEUED:
                # 在等重试退避的也让 worker 马上领走、按取消收尾
                job.next_attempt_at = min(job.next_attempt_at, now)
                await notify(session, f"pool:{job.pool}")
            else:
                await notify(session, f"cancel:{job.id}")
            await session.flush()
            await session.refresh(job)  # 刷新库里算的列（updated_at 等），免得在事务外懒加载
            return _row(job)

    async def retry(self, workspace_id: str, job_id: str) -> JobRow:
        now = self._clock()
        async with self._tx(workspace_id) as session:
            job = await self._get(session, job_id, lock=True)
            if job.status == RUNNING:
                raise JobConflict("任务正在运行")
            if job.status == SUCCEEDED:
                raise JobConflict("任务已经成功，不用重跑")
            if job.status == QUEUED:
                job.next_attempt_at, job.cancel_requested, job.canceled_by = now, False, None
            else:
                await self._requeue(session, job, now)
            job.updated_at = now
            await notify(session, f"pool:{job.pool}")
            await session.flush()
            await session.refresh(job)  # 刷新库里算的列（updated_at 等），免得在事务外懒加载
            return _row(job)

    async def delete(self, workspace_id: str, job_id: str) -> None:
        async with self._tx(workspace_id) as session:
            job = await self._get(session, job_id, lock=True)
            if job.status not in (FAILED, CANCELED):
                raise JobConflict("只能删除失败或已取消的任务记录")
            await session.execute(delete(Job).where(Job.id == job_id))

    async def _requeue(self, session: AsyncSession, job: Job, now: datetime) -> None:
        """失败、取消的重新排队。同一对象已经有排队中的同类任务时（部分唯一索引）不重复排。"""
        try:
            async with session.begin_nested():
                await session.execute(update(Job).where(Job.id == job.id).values(
                    status=QUEUED, tries=0, next_attempt_at=now, error_code=None, error_class=None, error_detail=None,
                    cancel_requested=False, canceled_by=None, stage=None, finished_at=None, updated_at=now))
        except IntegrityError as error:
            raise JobConflict("这个对象已经有一条排队中的同类任务，不用重跑") from error
        await session.execute(delete(JobStage).where(JobStage.job_id == job.id))
        await session.refresh(job)

    async def _get(self, session: AsyncSession, job_id: str, *, lock: bool = False) -> Job:
        query = select(Job).where(Job.id == job_id)
        job = await session.scalar(query.with_for_update() if lock else query)
        if job is None:
            raise JobNotFound()
        return job


def _pool_counts(now: datetime):
    queued, since = Job.status == QUEUED, now - RECENT
    return select(
        Job.pool.label("pool"),
        func.count().filter(and_(queued, Job.tries == 0)).label("queued"),
        func.count().filter(and_(queued, Job.tries > 0)).label("retrying"),
        func.count().filter(Job.status == RUNNING).label("running"),
        func.count().filter(Job.status == FAILED).label("failed"),
        func.min(Job.next_attempt_at).filter(and_(queued, Job.next_attempt_at <= now)).label("oldest"),
        func.count().filter(and_(Job.status == SUCCEEDED, Job.finished_at >= since)).label("succeeded_recent"),
        func.count().filter(and_(Job.status == FAILED, Job.finished_at >= since)).label("failed_recent"),
    ).group_by(Job.pool)


def _conditions(where: JobFilter) -> list:
    conditions = []
    if where.status == RETRYING:
        conditions += [Job.status == QUEUED, Job.tries > 0]
    elif where.status == QUEUED:
        conditions += [Job.status == QUEUED, Job.tries == 0]
    elif where.status:
        conditions.append(Job.status == where.status)
    if where.pool:
        conditions.append(Job.pool == where.pool)
    if where.kind:
        conditions.append(Job.kind == where.kind)
    return conditions


async def _cancelable(session: AsyncSession, kind: str) -> bool:
    """看在线 worker 报上来的任务类型；没有 worker 认得它时放行（标记留着，认得它的 worker 起来后再判断）。"""
    rows = (await session.scalars(select(JobWorker.kinds))).all()
    known = [k for kinds in rows for k in kinds or () if k.get("name") == kind]
    return not known or all(k.get("cancelable", True) for k in known)


def _n(stats: dict, pool: str, field: str) -> int:
    row = stats.get(pool)
    return int(getattr(row, field)) if row is not None else 0


def _wait(row, now: datetime) -> float | None:
    if row is None or row.oldest is None:
        return None
    return max(0.0, (now - row.oldest).total_seconds())


def _alive(worker: JobWorker, now: datetime) -> bool:
    return (now - worker.heartbeat_at).total_seconds() <= HEARTBEAT_STALE


def _worker(worker: JobWorker, now: datetime) -> WorkerInfo:
    return WorkerInfo(id=worker.id, host=worker.host, pid=worker.pid, version=worker.version,
                      pools=dict(worker.pools or {}), busy=dict(worker.busy or {}), alive=_alive(worker, now),
                      started_at=worker.started_at, heartbeat_at=worker.heartbeat_at)


def _gates(alive: list[JobWorker]) -> tuple[GateInfo, ...]:
    merged: dict[str, GateInfo] = {}
    for worker in alive:
        for name, use in (worker.gates or {}).items():
            seen = merged.get(name)
            merged = {**merged, name: GateInfo(
                name=name, holding=int(use.get("holding", 0)) + (seen.holding if seen else 0),
                limit=max(int(use.get("limit", 0)), seen.limit if seen else 0),
                shared=bool(use.get("shared")) and (seen.shared if seen else True))}
    return tuple(merged[name] for name in sorted(merged))


def _kinds(workers) -> tuple[KindInfo, ...]:
    """各 worker 报上来的任务类型合在一起（失联的也算：面板上要能认出它们排下的任务）。"""
    merged: dict[str, KindInfo] = {}
    for worker in workers:
        for kind in worker.kinds or ():
            name = str(kind.get("name") or "")
            if name:
                merged = {**merged, name: KindInfo(name=name, label=str(kind.get("label") or name),
                                                   pool=str(kind.get("pool") or ""),
                                                   cancelable=bool(kind.get("cancelable", True)))}
    return tuple(sorted(merged.values(), key=lambda k: (_pool_key(k.pool), k.name)))
