"""worker 主循环：NOTIFY 唤醒、单个任务崩溃不影响循环、优雅停机退回排队、宽限期后强停、清理旧任务行。"""

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import update

from knowwork_server.jobs import Job, JobError, JobRunner, listen

from .support import KIND, Clock, job, jobs, put, tx

pytestmark = pytest.mark.anyio


def engine_of(files_db):
    return files_db.sessionmaker.kw["bind"]


async def until(predicate, timeout=5.0):
    async def poll():
        while not await predicate():
            await asyncio.sleep(0.02)
    await asyncio.wait_for(poll(), timeout)


async def test_notify_wakes_an_idle_worker(files_db):
    done = asyncio.Event()

    async def handler(ctx):
        done.set()

    r = JobRunner(files_db.sessionmaker, clock=Clock())
    r.register(KIND, handler)
    stop, ready = asyncio.Event(), asyncio.Event()
    listener = asyncio.create_task(listen(engine_of(files_db), r.on_notify, stop, ready=ready))
    loop = asyncio.create_task(r.run_forever(lambda: ["ws1"], stop, idle_seconds=60))
    await asyncio.wait_for(ready.wait(), 5)
    await asyncio.sleep(0.1)  # 执行槽进入等待
    await put(files_db)
    await asyncio.wait_for(done.wait(), 5)  # 不用等 60 秒的轮询
    stop.set()
    await asyncio.wait_for(asyncio.gather(loop, listener), 5)


async def test_loop_survives_crashes_and_bad_workspace_lists(files_db):
    handled = []

    async def handler(ctx):
        handled.append(ctx.subject_id)
        if ctx.subject_id == "bad":
            raise JobError("corrupt", "坏文件", error_class="file")

    calls = iter([RuntimeError("列工作空间失败")])

    def workspaces():
        error = next(calls, None)
        if error is not None:
            raise error
        return ["ws1"]

    r = JobRunner(files_db.sessionmaker, clock=Clock())
    r.register(KIND, handler)
    await put(files_db, "bad")
    await put(files_db, "good")
    stop = asyncio.Event()
    loop = asyncio.create_task(r.run_forever(workspaces, stop, idle_seconds=0.02))

    async def settled():
        return {row.status for row in await jobs(files_db)} == {"failed", "succeeded"}

    await until(settled)
    stop.set()
    await asyncio.wait_for(loop, 5)
    assert sorted(handled) == ["bad", "good"]


async def test_stop_releases_job_at_checkpoint_without_counting_a_try(files_db):
    started = asyncio.Event()

    async def handler(ctx):
        async with ctx.stage("batch"):
            started.set()
            while True:
                await asyncio.sleep(0.02)
                ctx.checkpoint()

    r = JobRunner(files_db.sessionmaker, clock=Clock())
    r.register(KIND, handler)
    job_id = await put(files_db)
    stop = asyncio.Event()
    loop = asyncio.create_task(r.run_forever(lambda: ["ws1"], stop, idle_seconds=0.02, grace_seconds=5))
    await asyncio.wait_for(started.wait(), 5)
    stop.set()
    await asyncio.wait_for(loop, 3)
    row = await job(files_db, job_id)
    assert (row.status, row.tries, row.claim_token, row.lease_until) == ("queued", 0, None, None), "退回排队，不算一次尝试"


async def test_stop_cancels_jobs_still_running_after_grace(files_db):
    started = asyncio.Event()

    async def handler(ctx):
        started.set()
        await asyncio.sleep(3600)  # 等一个很慢的外部调用，没有检查点

    r = JobRunner(files_db.sessionmaker, clock=Clock())
    r.register(KIND, handler)
    job_id = await put(files_db)
    stop = asyncio.Event()
    loop = asyncio.create_task(r.run_forever(lambda: ["ws1"], stop, idle_seconds=0.02, grace_seconds=0.2))
    await asyncio.wait_for(started.wait(), 5)
    stop.set()
    await asyncio.wait_for(loop, 3)
    assert (await job(files_db, job_id)).status == "queued"


async def test_old_finished_jobs_are_pruned(files_db):
    clock = Clock()
    r = JobRunner(files_db.sessionmaker, clock=clock)
    r.register(KIND, lambda ctx: asyncio.sleep(0))
    ids = {name: await put(files_db, name) for name in ("done_old", "failed_old", "failed_recent", "queued")}
    async with tx(files_db) as session:
        for name, status, age in (("done_old", "succeeded", 8), ("failed_old", "failed", 31),
                                  ("failed_recent", "failed", 8)):
            await session.execute(update(Job).where(Job.id == ids[name]).values(
                status=status, finished_at=clock.now - timedelta(days=age)))
    await r._store.prune("ws1")
    assert sorted(row.subject_id for row in await jobs(files_db)) == ["failed_recent", "queued"]


async def test_settle_failure_leaves_job_to_lease_expiry(files_db, monkeypatch):
    async def handler(ctx):
        return None

    kind = replace(KIND, lease_seconds=60)
    clock = Clock()
    r = JobRunner(files_db.sessionmaker, clock=clock)
    r.register(kind, handler)
    job_id = await put(files_db, kind=kind)

    async def down(*args, **kwargs):
        raise ConnectionError("数据库断了")

    monkeypatch.setattr(r._store, "succeed", down)
    monkeypatch.setattr(r._store, "requeue", down)
    outcome = await r.run_once("ws1")
    assert (outcome.status, outcome.error_code) == ("lost", "settle_failed")
    assert (await job(files_db, job_id)).status == "running"
    monkeypatch.undo()
    clock.advance(61)
    assert (await r.run_once("ws1")).status == "succeeded", "租约过期后被重新领取"
