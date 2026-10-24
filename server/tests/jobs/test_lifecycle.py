"""执行生命周期：按失败类别重试与收尾、租约过期接手、旧领取者写不进、取消、超时、阶段记录。"""

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import select

from knowwork_server.jobs import Done, Job, JobError, JobRunner, JobStage, cancel

from .support import KIND, Clock, job, put, stages, tx

pytestmark = pytest.mark.anyio


class Recorder:
    """可编排的处理函数：每次执行按顺序取一个效果（异常就抛，协程函数就执行，其余当成返回值）。"""

    def __init__(self, *effects):
        self.effects = list(effects)
        self.calls = 0
        self.gave_up = []

    async def handle(self, ctx):
        self.calls += 1
        effect = self.effects.pop(0) if len(self.effects) > 1 else self.effects[0]
        async with ctx.stage("work", n=self.calls) as stage:
            if isinstance(effect, BaseException):
                raise effect
            if callable(effect):
                return await effect(ctx, stage)
            return effect

    async def give_up(self, session, claim, failure):
        self.gave_up.append((claim.subject_id, failure.code, failure.error_class, failure.message, failure.detail))


def build(files_db, recorder, kind=KIND, clock=None):
    clock = clock or Clock()
    r = JobRunner(files_db.sessionmaker, clock=clock)
    r.register(kind, recorder.handle, recorder.give_up)
    return r, clock


async def test_service_errors_back_off_then_give_up(files_db):
    rec = Recorder(JobError("busy", "服务忙", error_class="service"))
    r, clock = build(files_db, rec)
    job_id = await put(files_db)
    assert (await r.run_once("ws1")).status == "retry"
    assert await r.run_once("ws1") is None, "退避期内不领"
    clock.advance(10)
    assert (await r.run_once("ws1")).status == "retry"
    clock.advance(19)
    assert await r.run_once("ws1") is None, "第二次退避 20 秒"
    clock.advance(1)
    outcome = await r.run_once("ws1")
    assert (outcome.status, outcome.error_code, rec.calls) == ("failed", "busy", 3)
    assert rec.gave_up == [("s1", "busy", "service", "服务忙", None)], "只有最后一次失败才交给业务收尾"
    row = await job(files_db, job_id)
    assert (row.status, row.tries, row.error_class, row.error_detail) == ("failed", 3, "service", "服务忙")
    assert await stages(files_db, job_id) == [(1, "work", "failed", "busy"), (2, "work", "failed", "busy"),
                                              (3, "work", "failed", "busy")]


async def test_retry_hook_tells_the_business_when_the_next_try_is(files_db):
    """失败后排好了重试：业务据此显示「等待重试」和原因；最后一次失败只走 give_up。"""
    rec = Recorder(JobError("busy", "服务忙", error_class="service"))
    retries = []

    async def on_retry(session, claim, failure, retry_at):
        retries.append((claim.subject_id, claim.tries, failure.code, failure.message, retry_at))

    clock = Clock()
    r = JobRunner(files_db.sessionmaker, clock=clock)
    r.register(KIND, rec.handle, rec.give_up, on_retry)
    await put(files_db)
    started = clock.now
    assert (await r.run_once("ws1")).status == "retry"
    assert retries == [("s1", 1, "busy", "服务忙", started + timedelta(seconds=10))]
    clock.advance(10)
    await r.run_once("ws1")
    clock.advance(20)
    assert (await r.run_once("ws1")).status == "failed"
    assert [t[1] for t in retries] == [1, 2], "第 3 次是最后一次，不再说要重试"
    assert [g[1] for g in rec.gave_up] == ["busy"]


async def test_file_errors_and_explicit_non_retryable_fail_at_once(files_db):
    for error in (JobError("corrupt", "文件损坏", error_class="file"),
                  JobError("not_configured", "未配置", error_class="service", retryable=False)):
        rec = Recorder(error)
        r, _ = build(files_db, rec)
        await put(files_db, error.code)
        assert (await r.run_once("ws1")).status == "failed"
        assert rec.calls == 1 and rec.gave_up[0][1] == error.code


async def test_retry_after_overrides_backoff(files_db):
    rec = Recorder(JobError("locked", "被占用", retry_after=1.0), None)
    r, clock = build(files_db, rec)
    await put(files_db)
    assert (await r.run_once("ws1")).status == "retry"
    clock.advance(1)
    assert (await r.run_once("ws1")).status == "succeeded"


async def test_crashes_are_internal_errors_retried_twice(files_db):
    rec = Recorder(KeyError("boom"))
    r, clock = build(files_db, rec, replace(KIND, max_tries=6))
    job_id = await put(files_db, kind=replace(KIND, max_tries=6))
    assert (await r.run_once("ws1")).status == "retry"
    clock.advance(30)
    assert (await r.run_once("ws1")).status == "retry"
    clock.advance(120)
    outcome = await r.run_once("ws1")
    assert (outcome.status, outcome.error_code, rec.calls) == ("failed", "internal_error", 3), "内部错误最多再试两次"
    row = await job(files_db, job_id)
    assert row.error_class == "internal" and "KeyError: 'boom'" in row.error_detail, "记完整堆栈"



async def test_finisher_failure_rolls_back_and_retries(files_db):
    written = []

    async def bad_apply(session):
        written.append(1)
        raise RuntimeError("写业务结果失败")

    rec = Recorder(Done(apply=bad_apply), None)
    r, clock = build(files_db, rec)
    job_id = await put(files_db)
    outcome = await r.run_once("ws1")
    assert (outcome.status, outcome.error_code) == ("retry", "finish_failed")
    assert (await job(files_db, job_id)).status == "queued"
    clock.advance(30)
    assert (await r.run_once("ws1")).status == "succeeded"


async def test_give_up_hook_failure_still_marks_failed(files_db):
    rec = Recorder(JobError("corrupt", "坏文件", error_class="file"))

    async def broken(session, claim, failure):
        raise RuntimeError("业务收尾也坏了")

    r = JobRunner(files_db.sessionmaker, clock=Clock())
    r.register(KIND, rec.handle, broken)
    job_id = await put(files_db)
    assert (await r.run_once("ws1")).status == "failed"
    assert (await job(files_db, job_id)).status == "failed", "不会因为收尾坏了反复被领取"


async def test_expired_lease_is_taken_over_and_stale_owner_cannot_write(files_db):
    rec = Recorder(None)
    r, clock = build(files_db, rec)
    job_id = await put(files_db)
    stale = await r.claim("ws1")  # 这个 worker 卡住了
    assert await r.claim("ws1") is None
    clock.advance(61)
    assert (await r.run_once("ws1")).status == "succeeded", "另一个 worker 接手"
    assert (await r.execute(stale)).status == "lost", "旧领取者回来，什么都写不进"
    row = await job(files_db, job_id)
    assert (row.status, row.tries) == ("succeeded", 2)


async def test_repeated_interruptions_give_up_with_stage_detail(files_db):
    async def hang(ctx, stage):
        await asyncio.sleep(3600)

    rec = Recorder(hang)
    kind = replace(KIND, max_tries=2)
    r, clock = build(files_db, rec, kind)
    job_id = await put(files_db, kind=kind)
    for _ in range(2):
        claim = await r.claim("ws1")
        await r._store.open_stage(claim, 60, "parse", {})  # 进了 parse 阶段后 worker 被杀
        clock.advance(61)
    outcome = await r.run_once("ws1")
    assert (outcome.status, outcome.error_code) == ("failed", "interrupted")
    [(_subject, code, cls, message, detail)] = rec.gave_up
    assert (code, cls, message) == ("interrupted", "internal", "处理多次中断，已停止")
    assert "「parse」阶段中断 2 次" in detail
    assert await stages(files_db, job_id) == [(1, "parse", "failed", "interrupted"),
                                              (2, "parse", "failed", "interrupted")]


async def test_cancel_reaches_a_running_job(files_db):
    started = asyncio.Event()

    async def wait(ctx, stage):
        started.set()
        await asyncio.sleep(3600)

    rec = Recorder(wait)
    kind = replace(KIND, lease_seconds=0.3)
    r, _ = build(files_db, rec, kind)
    job_id = await put(files_db, kind=kind)
    run = asyncio.create_task(r.run_once("ws1"))
    await started.wait()
    async with tx(files_db) as session:
        await cancel(session, kind, ["s1"])
    outcome = await asyncio.wait_for(run, timeout=5)
    assert outcome.status == "canceled", "续租时发现取消标记，直接停掉等待中的外部调用"
    assert (await job(files_db, job_id)).status == "canceled"
    assert await stages(files_db, job_id) == [(1, "work", "canceled", None)]
    assert rec.gave_up == []


async def test_cancel_notification_skips_the_renew_wait(files_db):
    started = asyncio.Event()

    async def wait(ctx, stage):
        started.set()
        await asyncio.sleep(3600)

    rec = Recorder(wait)
    r, _ = build(files_db, rec)  # 租约 60 秒，续租间隔 20 秒
    job_id = await put(files_db)
    run = asyncio.create_task(r.run_once("ws1"))
    await started.wait()
    async with tx(files_db) as session:
        await cancel(session, KIND, ["s1"])
    r.on_notify(f"cancel:{job_id}")  # LISTEN 收到的通知
    assert (await asyncio.wait_for(run, timeout=5)).status == "canceled"


async def test_lost_lease_stops_the_handler(files_db):
    started = asyncio.Event()

    async def wait(ctx, stage):
        started.set()
        await asyncio.sleep(3600)

    kind = replace(KIND, lease_seconds=0.3)
    r, _ = build(files_db, Recorder(wait), kind)
    job_id = await put(files_db, kind=kind)
    run = asyncio.create_task(r.run_once("ws1"))
    await started.wait()
    async with tx(files_db) as session:  # 模拟别的 worker 已经接手
        row = await session.get(Job, job_id)
        row.claim_token = "someone-else"
    assert (await asyncio.wait_for(run, timeout=5)).status == "lost"


async def test_overall_timeout_and_extension(files_db):
    async def slow(ctx, stage):
        await asyncio.sleep(0.3)

    async def extended(ctx, stage):
        ctx.extend_deadline(2)
        await asyncio.sleep(0.3)
        return Done(note="extended")

    kind = replace(KIND, timeout_seconds=0.1)
    r, clock = build(files_db, Recorder(slow, extended), kind)
    await put(files_db, kind=kind)
    outcome = await r.run_once("ws1")
    assert (outcome.status, outcome.error_code) == ("retry", "job_timeout")
    clock.advance(10)
    assert (await r.run_once("ws1")).error_code == "extended", "处理函数按工作量延长时限"


async def test_retry_is_superseded_by_a_newer_request(files_db):
    async def fail_after_new_request(ctx, stage):
        await put(files_db, payload={"attempt": 2})  # 执行期间用户又点了重新解析
        raise JobError("busy", "服务忙")

    rec = Recorder(fail_after_new_request, None)
    r, _ = build(files_db, rec)
    first = await put(files_db, payload={"attempt": 1})
    assert (await r.run_once("ws1")).status == "superseded"
    assert (await job(files_db, first)).status == "canceled"
    assert (await r.claim("ws1")).payload == {"attempt": 2}


async def test_stage_records_detail(files_db):
    async def work(ctx, stage):
        stage.record(pages=12)

    r, _ = build(files_db, Recorder(work))
    job_id = await put(files_db)
    await r.run_once("ws1")
    async with tx(files_db) as session:
        [row] = (await session.scalars(select(JobStage).where(JobStage.job_id == job_id))).all()
    assert (row.status, row.detail, row.finished_at is not None) == ("done", {"n": 1, "pages": 12}, True)
