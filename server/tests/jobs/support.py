"""任务框架测试的共用工具：可拨动的时钟、在指定空间里入队、读任务行与阶段行。"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from knowwork_server.access.rls import set_workspace
from knowwork_server.jobs import Job, JobKind, JobStage, enqueue

KIND = JobKind("test.work", lease_seconds=60, retry_delays=(10.0, 20.0), max_tries=3)


class Clock:
    def __init__(self):
        self.now = datetime.now(UTC) + timedelta(seconds=5)  # 晚于库里 now() 写下的排队时间

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


@asynccontextmanager
async def tx(db, workspace_id="ws1"):
    async with db.sessionmaker() as session, session.begin():
        await set_workspace(session, workspace_id)
        yield session


async def put(db, subject="s1", kind=KIND, workspace_id="ws1", **options):
    async with tx(db, workspace_id) as session:
        return await enqueue(session, kind, workspace_id=workspace_id, subject_id=subject, **options)


async def jobs(db, workspace_id="ws1"):
    async with tx(db, workspace_id) as session:
        return (await session.scalars(select(Job).order_by(Job.created_at, Job.id))).all()


async def job(db, job_id, workspace_id="ws1"):
    async with tx(db, workspace_id) as session:
        return await session.get(Job, job_id)


async def stages(db, job_id, workspace_id="ws1"):
    async with tx(db, workspace_id) as session:
        rows = (await session.scalars(select(JobStage).where(JobStage.job_id == job_id)
                                      .order_by(JobStage.started_at, JobStage.id))).all()
    return [(s.try_no, s.name, s.status, s.error_code) for s in rows]
