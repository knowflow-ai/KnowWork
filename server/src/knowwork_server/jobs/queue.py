"""入队与取消。都在调用方的事务里执行，和业务数据一起提交或一起回滚：不会出现「文档在、任务没了」。

NOTIFY 也在同一事务里发，PostgreSQL 在提交时才投递，worker 被唤醒时一定看得到这条任务；事务回滚就不投递。
"""

import uuid
from collections.abc import Sequence
from datetime import timedelta

from sqlalchemy import func, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .kinds import JobKind
from .models import CANCELED, QUEUED, RUNNING, Job

CHANNEL = "kw_jobs"
QUEUED_ONLY = text("status = 'queued'")


async def notify(session: AsyncSession, message: str) -> None:
    await session.execute(text("SELECT pg_notify(:channel, :message)"), {"channel": CHANNEL, "message": message})


async def enqueue(session: AsyncSession, kind: JobKind, *, workspace_id: str, subject_id: str,
                  payload: dict | None = None, priority: int | None = None, delay: float = 0.0) -> str:
    """排一条任务，返回任务 id。同一对象已有排队中的同类任务时合并进去：

    载荷换成最新的（重新解析带的是新轮次），优先级取高的，执行时间取早的，尝试次数清零（这是一次新的请求）；
    运行面板上刚取消、还没被 worker 收尾的那条也一样合并，取消作废。
    """
    stmt = insert(Job).values(
        id=f"job_{uuid.uuid4().hex}", workspace_id=workspace_id, kind=kind.name, subject_id=subject_id,
        pool=kind.pool, priority=kind.priority if priority is None else priority, payload=payload or {},
        version=kind.version, status=QUEUED, tries=0, max_tries=kind.max_tries, cancel_requested=False,
        next_attempt_at=func.now() + timedelta(seconds=delay))
    stmt = stmt.on_conflict_do_update(
        index_elements=[Job.workspace_id, Job.kind, Job.subject_id], index_where=QUEUED_ONLY,
        set_={"payload": stmt.excluded.payload, "version": stmt.excluded.version,
              "priority": func.greatest(Job.priority, stmt.excluded.priority),
              "next_attempt_at": func.least(Job.next_attempt_at, stmt.excluded.next_attempt_at),
              "tries": 0, "max_tries": stmt.excluded.max_tries, "error_code": None, "error_class": None,
              "error_detail": None, "cancel_requested": False, "canceled_by": None, "updated_at": func.now()},
    ).returning(Job.id)
    job_id = await session.scalar(stmt)
    await notify(session, f"pool:{kind.pool}")
    return job_id


async def cancel(session: AsyncSession, kind: JobKind, subject_ids: Sequence[str]) -> None:
    """取消这些对象的同类任务：排队中的直接改成已取消；运行中的打上取消标记并通知 worker，
    worker 在下一个检查点或续租时停下。不可取消的任务类型什么都不做。"""
    if not kind.cancelable or not subject_ids:
        return
    scope = (Job.kind == kind.name, Job.subject_id.in_(list(subject_ids)))
    await session.execute(update(Job).where(*scope, Job.status == QUEUED).values(
        status=CANCELED, error_code="canceled", finished_at=func.now(), updated_at=func.now()))
    running = await session.scalars(update(Job).where(*scope, Job.status == RUNNING).values(
        cancel_requested=True, updated_at=func.now()).returning(Job.id))
    for job_id in running.all():
        await notify(session, f"cancel:{job_id}")
