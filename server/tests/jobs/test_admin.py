"""运行面板：取消走业务的失败收尾、运行中不可取消的类型拦住、立即重跑、删除记录、各池统计、翻页、
worker 心跳；接口只给系统管理员、拒绝 API Key。"""

import asyncio
from dataclasses import replace
from datetime import timedelta

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from sqlalchemy import select

from knowwork_server.auth.principal import Principal
from knowwork_server.jobs import JobError, JobRunner
from knowwork_server.jobs.admin import JobConflict, JobFilter, JobNotFound, JobsAdmin
from knowwork_server.jobs.api import build_router
from knowwork_server.jobs.gate import LocalGate
from knowwork_server.jobs.heartbeat import Heartbeat
from knowwork_server.jobs.models import JobWorker

from .support import KIND, Clock, job, put, stages, tx
from .test_lifecycle import Recorder, build

pytestmark = pytest.mark.anyio


def panel(files_db, clock):
    return JobsAdmin(files_db.sessionmaker, "ws1", clock=clock)


async def test_cancel_of_a_queued_job_settles_through_the_business_give_up(files_db):
    rec = Recorder(None)
    r, clock = build(files_db, rec)
    job_id = await put(files_db)
    row = await panel(files_db, clock).cancel("root", job_id)
    assert row.cancel_requested and row.status == "queued"
    outcome = await r.run_once("ws1")
    assert (outcome.status, rec.calls) == ("canceled", 0), "没开始的不执行，直接收尾"
    assert rec.gave_up == [("s1", "canceled_by_admin", "internal", "系统管理员取消了这次处理", None)], \
        "业务不知道面板上取消了，要它按失败收尾，文档才不会一直停在处理中"
    assert (await job(files_db, job_id)).status == "canceled"


async def test_cancel_of_a_job_waiting_for_retry_is_settled_at_once(files_db):
    rec = Recorder(JobError("busy", "服务忙"))
    r, clock = build(files_db, rec)
    job_id = await put(files_db)
    assert (await r.run_once("ws1")).status == "retry"
    assert await r.run_once("ws1") is None, "在等 10 秒的退避"
    await panel(files_db, clock).cancel("root", job_id)
    assert (await r.run_once("ws1")).status == "canceled", "取消不用等退避"
    assert [g[1] for g in rec.gave_up] == ["canceled_by_admin"]


async def test_cancel_of_a_running_job_stops_it_and_runs_the_give_up(files_db):
    started = asyncio.Event()

    async def wait(ctx, stage):
        started.set()
        await asyncio.sleep(3600)

    rec = Recorder(wait)
    r, clock = build(files_db, rec)
    job_id = await put(files_db)
    run = asyncio.create_task(r.run_once("ws1"))
    await started.wait()
    await panel(files_db, clock).cancel("root", job_id)
    r.on_notify(f"cancel:{job_id}")
    assert (await asyncio.wait_for(run, timeout=5)).status == "canceled"
    assert [g[1] for g in rec.gave_up] == ["canceled_by_admin"]
    assert await stages(files_db, job_id) == [(1, "work", "canceled", None)]


async def test_running_jobs_of_non_cancelable_kinds_cannot_be_canceled(files_db):
    kind = replace(KIND, cancelable=False, label="删除测试")
    rec = Recorder(None)
    r, clock = build(files_db, rec, kind)
    await Heartbeat(files_db.sessionmaker, r, LocalGate(), version="1").beat()  # 面板从心跳知道它不能中途停
    job_id = await put(files_db, kind=kind)
    admin = panel(files_db, clock)
    await r.claim("ws1")
    with pytest.raises(JobConflict, match="半截"):
        await admin.cancel("root", job_id)
    other = await put(files_db, "s2", kind=kind)
    assert (await admin.cancel("root", other)).cancel_requested, "还没开始的照样能取消"


async def test_a_new_request_merged_into_a_canceled_job_revokes_the_cancel(files_db):
    _, clock = build(files_db, Recorder(None))
    job_id = await put(files_db)
    await panel(files_db, clock).cancel("root", job_id)
    assert await put(files_db) == job_id, "合并进同一条"
    row = await job(files_db, job_id)
    assert (row.cancel_requested, row.canceled_by) == (False, None), "用户又发起了一次，取消作废"


async def test_retry_of_a_failed_job_is_a_new_request(files_db):
    rec = Recorder(JobError("corrupt", "坏了", error_class="file"), None)
    r, clock = build(files_db, rec)
    job_id = await put(files_db)
    assert (await r.run_once("ws1")).status == "failed"
    row = await panel(files_db, clock).retry(job_id)
    assert (row.status, row.tries, row.error_code, row.finished_at) == ("queued", 0, None, None)
    assert await stages(files_db, job_id) == [], "上一轮的阶段记录清掉"
    assert (await r.run_once("ws1")).status == "succeeded"
    assert await stages(files_db, job_id) == [(1, "work", "done", None)]


async def test_retry_does_not_duplicate_a_queued_sibling(files_db):
    rec = Recorder(JobError("corrupt", "坏了", error_class="file"))
    r, clock = build(files_db, rec)
    failed = await put(files_db)
    await r.run_once("ws1")
    await put(files_db)  # 用户又点了一次，已经排着
    with pytest.raises(JobConflict, match="已经有一条排队中"):
        await panel(files_db, clock).retry(failed)
    assert (await job(files_db, failed)).status == "failed", "冲突时原样不动"


async def test_retry_of_a_queued_job_skips_the_backoff(files_db):
    rec = Recorder(JobError("busy", "服务忙"), None)
    r, clock = build(files_db, rec)
    job_id = await put(files_db)
    await r.run_once("ws1")
    assert await r.run_once("ws1") is None
    await panel(files_db, clock).retry(job_id)
    assert (await r.run_once("ws1")).status == "succeeded"


async def test_actions_check_the_job_status(files_db):
    rec = Recorder(None)
    r, clock = build(files_db, rec)
    admin = panel(files_db, clock)
    job_id = await put(files_db)
    with pytest.raises(JobConflict, match="只能删除"):
        await admin.delete(job_id)
    await r.run_once("ws1")
    with pytest.raises(JobConflict, match="已经结束"):
        await admin.cancel("root", job_id)
    with pytest.raises(JobConflict, match="已经成功"):
        await admin.retry(job_id)
    with pytest.raises(JobNotFound):
        await admin.retry("job_missing")

    rec2 = Recorder(JobError("corrupt", "坏了", error_class="file"))
    r2, _ = build(files_db, rec2)
    failed = await put(files_db, "s2")
    await r2.run_once("ws1")
    await admin.delete(failed)
    assert await job(files_db, failed) is None


async def test_overview_counts_each_pool_and_reads_the_heartbeats(files_db):
    rec = Recorder(JobError("busy", "服务忙"), JobError("corrupt", "坏了", error_class="file"), None)
    r, clock = build(files_db, rec)
    await put(files_db, "a")
    await r.run_once("ws1")                   # a 等重试
    await put(files_db, "b")
    await r.run_once("ws1")                   # b 失败
    await put(files_db, "c")
    await r.run_once("ws1")                   # c 成功
    await put(files_db, "d")                  # d 排队
    other = replace(KIND, name="test.batch", pool="maintenance", label="批量")
    await put(files_db, "e", kind=other)
    worker = Heartbeat(files_db.sessionmaker, r, LocalGate(), version="1.0", clock=clock)
    await worker.beat()
    async with tx(files_db) as session:  # 一个早就失联的 worker：不算执行槽，但列出来
        session.add(JobWorker(id="gone", host="h", pid=1, version="0.9", pools={"core": 9}, busy={}, kinds=[],
                              gates={}, started_at=clock() - timedelta(hours=2),
                              heartbeat_at=clock() - timedelta(minutes=5)))

    clock.advance(4)
    found = await panel(files_db, clock).overview()
    pools = {p.pool: p for p in found.pools}
    assert list(pools) == ["core", "maintenance"], "按 core、enrichment、maintenance 的顺序"
    core = pools["core"]
    assert (core.slots, core.queued, core.retrying, core.running, core.failed) == (2, 1, 1, 0, 1)
    assert (core.succeeded_recent, core.failed_recent) == (1, 1)
    assert core.oldest_wait_seconds is not None and core.oldest_wait_seconds >= 4, "d 到点后一直没人领"
    assert pools["maintenance"].slots == 0, "没有 worker 跑维护池"
    assert {w.id: w.alive for w in found.workers} == {"gone": False, worker.id: True}
    assert [(k.name, k.label, k.cancelable) for k in found.kinds] == [("test.work", "test.work", True)]


async def test_jobs_are_filtered_and_paged_newest_first(files_db):
    rec = Recorder(JobError("busy", "服务忙"))
    r, clock = build(files_db, rec)
    ids = [await put(files_db, f"s{i}") for i in range(5)]
    await r.run_once("ws1")  # 最早的那条等重试
    admin = panel(files_db, clock)
    first = await admin.jobs(JobFilter(), limit=2)
    second = await admin.jobs(JobFilter(), cursor=first.next, limit=2)
    third = await admin.jobs(JobFilter(), cursor=second.next, limit=2)
    seen = [row.id for page in (first, second, third) for row in page.items]
    assert sorted(seen) == sorted(ids) and len(seen) == 5 and third.next is None
    assert [row.id for row in (await admin.jobs(JobFilter(status="retrying"))).items] == [ids[0]]
    assert len((await admin.jobs(JobFilter(status="queued"))).items) == 4
    assert (await admin.jobs(JobFilter(kind="other"))).items == ()
    with pytest.raises(JobConflict, match="翻页"):
        await admin.jobs(JobFilter(), cursor="乱写的")
    detail, steps = await admin.detail(ids[0])
    assert detail.error_code == "busy" and [(s.name, s.status, s.error_code) for s in steps] == [
        ("work", "failed", "busy")]


async def test_heartbeat_reports_slots_and_gates_and_leaves_on_stop(files_db):
    gate = LocalGate()
    r = JobRunner(files_db.sessionmaker, pools={"core": 3, "maintenance": 1})
    r.register(replace(KIND, label="测试"), Recorder(None).handle)
    async with gate.hold("mineru", 2):
        beat = Heartbeat(files_db.sessionmaker, r, gate, version="1.0", every=0.05)
        stop = asyncio.Event()
        task = asyncio.create_task(beat.run(stop))
        await asyncio.sleep(0.1)
        async with files_db.sessionmaker() as session:
            row = await session.get(JobWorker, beat.id)
        assert row.pools == {"core": 3}, "没注册任务类型的池不起执行槽"
        assert row.kinds == [{"name": "test.work", "label": "测试", "pool": "core", "cancelable": True}]
        assert row.gates == {"mineru": {"holding": 1, "limit": 2, "shared": False}}
        stop.set()
        await task
    async with files_db.sessionmaker() as session:
        assert (await session.scalars(select(JobWorker))).all() == [], "正常停机删掉自己"


async def test_stale_heartbeats_are_forgotten_after_a_day(files_db):
    clock = Clock()
    r = JobRunner(files_db.sessionmaker)
    async with files_db.sessionmaker() as session, session.begin():
        session.add(JobWorker(id="killed", host="h", pid=1, version="1", pools={}, busy={}, kinds=[], gates={},
                              started_at=clock() - timedelta(days=3), heartbeat_at=clock() - timedelta(days=2)))
    await Heartbeat(files_db.sessionmaker, r, LocalGate(), version="1", clock=clock).beat()
    async with files_db.sessionmaker() as session:
        assert await session.get(JobWorker, "killed") is None, "被强杀的 worker 留下的行"


async def fake_principal(request: Request) -> Principal:
    user = request.headers.get("x-user")
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    method = "api_key" if request.headers.get("x-key") else "session"
    return Principal(user_id=user, auth_method=method, scopes=frozenset() if method == "api_key" else None)


async def only_admin(user_id: str) -> bool:
    return user_id == "admin"


async def test_api_is_for_the_system_admin_in_the_browser_only(files_db):
    rec = Recorder(JobError("corrupt", "坏了", error_class="file"))
    r, clock = build(files_db, rec)
    job_id = await put(files_db)
    await r.run_once("ws1")
    app = FastAPI()
    app.include_router(build_router(panel(files_db, clock), fake_principal, only_admin))
    admin = {"x-user": "admin"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        assert (await client.get("/api/system/jobs/overview", headers={"x-user": "u2"})).status_code == 403
        by_key = await client.get("/api/system/jobs", headers={**admin, "x-key": "1"})
        assert (by_key.status_code, by_key.json()["detail"]) == (403, "运行面板只能在网页上使用")

        overview = (await client.get("/api/system/jobs/overview", headers=admin)).json()
        assert overview["pools"][0]["failed"] == 1
        listed = (await client.get("/api/system/jobs", params={"status": "failed"}, headers=admin)).json()
        assert [item["id"] for item in listed["items"]] == [job_id] and listed["next"] is None
        assert "payload" not in listed["items"][0] and "error_detail" not in listed["items"][0], "不给载荷与错误详情"
        assert (await client.get("/api/system/jobs", params={"status": "bogus"}, headers=admin)).status_code == 422
        detail = (await client.get(f"/api/system/jobs/{job_id}", headers=admin)).json()
        assert detail["stages"][0]["error_code"] == "corrupt"

        conflict = await client.post(f"/api/system/jobs/{job_id}/cancel", headers=admin)
        assert (conflict.status_code, conflict.json()["detail"]) == (409, "任务已经结束，不用取消")
        assert (await client.post(f"/api/system/jobs/{job_id}/retry", headers=admin)).json()["status"] == "queued"
        assert (await client.post(f"/api/system/jobs/{job_id}/cancel", headers=admin)).json()["cancel_requested"]
        assert (await client.delete("/api/system/jobs/job_missing", headers=admin)).status_code == 404
