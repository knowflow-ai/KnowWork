"""入队与领取：同一事务、合并排队、SKIP LOCKED、同一对象串行、载荷版本、工作空间隔离。"""

import asyncio
from dataclasses import replace

import pytest

from knowwork_server.jobs import Done, Job, JobRunner, cancel, enqueue

from .support import KIND, Clock, job, jobs, put, tx

pytestmark = pytest.mark.anyio


async def noop(ctx):
    return None


def runner(files_db, clock=None, kinds=(KIND,)):
    r = JobRunner(files_db.sessionmaker, clock=clock or Clock())
    for kind in kinds:
        r.register(kind, noop)
    return r


async def test_enqueue_commits_with_business_transaction(files_db):
    with pytest.raises(RuntimeError):
        async with tx(files_db) as session:
            await enqueue(session, KIND, workspace_id="ws1", subject_id="s1")
            raise RuntimeError("业务写失败")
    assert await jobs(files_db) == [], "业务事务回滚，任务一起没了"
    await put(files_db)
    [row] = await jobs(files_db)
    assert (row.kind, row.subject_id, row.status, row.pool, row.max_tries) == ("test.work", "s1", "queued", "core", 3)


async def test_queued_requests_for_same_subject_are_merged(files_db):
    first = await put(files_db, payload={"attempt": 1}, priority=1, delay=600)
    second = await put(files_db, payload={"attempt": 2}, priority=0)
    assert first == second
    [row] = await jobs(files_db)
    assert (row.payload, row.priority) == ({"attempt": 2}, 1), "载荷换成最新的，优先级取高的"
    assert (await runner(files_db).claim("ws1")).payload == {"attempt": 2}, "执行时间取早的，不再等 600 秒"


async def test_concurrent_claims_never_share_a_job(files_db):
    for subject in ("a", "b", "c"):
        await put(files_db, subject)
    r = runner(files_db)
    claims = await asyncio.gather(*(r.claim("ws1") for _ in range(5)))
    taken = sorted(c.subject_id for c in claims if c is not None)
    assert taken == ["a", "b", "c"]
    assert await r.claim("ws2") is None, "别的工作空间看不到"


async def test_same_subject_runs_one_at_a_time(files_db):
    r = runner(files_db)
    await put(files_db, payload={"attempt": 1})
    running = await r.claim("ws1")
    await put(files_db, payload={"attempt": 2})  # 运行中再来一次请求：排在后面，不合并进正在跑的那次
    assert len(await jobs(files_db)) == 2
    assert await r.claim("ws1") is None, "同一对象有同类任务在跑，排队的那条不领"
    await put(files_db, "other")
    assert (await r.claim("ws1")).subject_id == "other", "别的对象不受影响"
    assert (await r.execute(running)).status == "succeeded"
    assert (await r.claim("ws1")).payload == {"attempt": 2}


async def test_priority_orders_claims(files_db):
    await put(files_db, "low")
    await put(files_db, "high", priority=10)
    r = runner(files_db)
    assert [(await r.claim("ws1")).subject_id for _ in range(2)] == ["high", "low"]


async def test_backoff_is_respected(files_db):
    clock = Clock()
    await put(files_db, delay=60)
    r = runner(files_db, clock)
    assert await r.claim("ws1") is None
    clock.advance(60)
    assert await r.claim("ws1") is not None


async def test_unknown_kinds_and_newer_payload_versions_stay_queued(files_db):
    newer = replace(KIND, version=2)
    await put(files_db, "v2", kind=newer)
    await put(files_db, "other", kind=replace(KIND, name="test.other"))
    old_worker = runner(files_db)
    assert await old_worker.claim("ws1") is None, "旧 worker 不领新版本载荷，也不领没注册的类型"
    rows = await jobs(files_db)
    assert {r.status for r in rows} == {"queued"}, "不认得的不判失败"
    assert (await runner(files_db, kinds=(newer,)).claim("ws1")).subject_id == "v2"


async def test_pools_are_isolated(files_db):
    slow = replace(KIND, name="test.sync", pool="maintenance")
    await put(files_db, "batch", kind=slow)
    r = runner(files_db, kinds=(KIND, slow))
    assert await r.claim("ws1", "core") is None
    assert (await r.claim("ws1", "maintenance")).subject_id == "batch"


async def test_cancel_queued_and_running(files_db):
    r = runner(files_db)
    await put(files_db, "running")
    claim = await r.claim("ws1")
    await put(files_db, "queued")
    async with tx(files_db) as session:
        await cancel(session, KIND, ["running", "queued"])
    rows = {row.subject_id: row for row in await jobs(files_db)}
    assert (rows["queued"].status, rows["queued"].error_code) == ("canceled", "canceled")
    assert (rows["running"].status, rows["running"].cancel_requested) == ("running", True), "运行中的只打标记"
    assert await r.claim("ws1") is None
    stuck = replace(KIND, cancelable=False)
    async with tx(files_db) as session:
        await cancel(session, stuck, ["running"])  # 不可取消的类型什么都不做
    assert (await job(files_db, claim.job_id)).status == "running"


async def test_finisher_runs_inside_the_success_transaction(files_db):
    seen = []

    async def handler(ctx):
        async def apply(session):
            seen.append((await session.get(Job, ctx.claim.job_id)).claim_token)
        return Done(apply=apply, note="partial")

    r = JobRunner(files_db.sessionmaker, clock=Clock())
    r.register(KIND, handler)
    job_id = await put(files_db)
    outcome = await r.run_once("ws1")
    assert (outcome.status, outcome.error_code) == ("succeeded", "partial")
    assert len(seen) == 1 and seen[0], "收尾时任务还锁在自己手里，领取令牌还在"
    row = await job(files_db, job_id)
    assert (row.status, row.claim_token, row.error_code, row.finished_at is not None) == (
        "succeeded", None, "partial", True)
