"""worker 进程：真的会领取并执行后台任务；收到停止信号即退出；配置不完整立即失败。"""

import asyncio

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from knowwork_server.access.service import AccessService
from knowwork_server.attachments import AttachmentSettings
from knowwork_server.attachments.parse_job import parse_kind
from knowwork_server.config import get_settings
from knowwork_server.db.base import in_workspace, make_engine, make_sessionmaker, make_workspace_sessionmaker
from knowwork_server.db.migrate import upgrade_to_head
from knowwork_server.jobs import Job, enqueue
from knowwork_server.ops import worker

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def fresh_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_worker_runs_attachment_parse_jobs(app_db, monkeypatch, tmp_path):
    await upgrade_to_head(app_db.owner_url, app_role=app_db.role)
    for name, value in {"KNOWWORK_MASTER_KEY": "k" * 40, "KNOWWORK_DATABASE_URL": app_db.app_url,
                        "KNOWWORK_BLOB_DIR": str(tmp_path), "KNOWWORK_JOB_IDLE_SECONDS": "0.1"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("KNOWWORK_SANDBOX_DOCKER_ENABLED", raising=False)
    engine = make_engine(app_db.app_url)
    # worker 每轮从工作空间表读有哪些空间，空间要先在（实际部署里初始化向导建好）
    await AccessService(make_sessionmaker(engine)).create_workspace(workspace_id="default", name="默认空间",
                                                                     owner_user_id="u1")
    scoped = make_workspace_sessionmaker(engine)
    with in_workspace("default"):
        async with scoped() as session, session.begin():  # 附件已经被删了的解析任务：处理函数什么都不做，正常结束
            job_id = await enqueue(session, parse_kind(AttachmentSettings()), workspace_id="default",
                                   subject_id="att_gone")
    stop = asyncio.Event()
    task = asyncio.create_task(worker.run(stop))

    async def status():
        with in_workspace("default"):
            async with scoped() as session, session.begin():
                return await session.scalar(select(Job.status).where(Job.id == job_id))

    async def until_done():
        while await status() != "succeeded":
            await asyncio.sleep(0.05)

    try:
        await asyncio.wait_for(until_done(), 10)
    finally:
        stop.set()
        await asyncio.wait_for(task, 10)
        await engine.dispose()


async def test_worker_fails_fast_without_master_key(monkeypatch):
    monkeypatch.delenv("KNOWWORK_MASTER_KEY", raising=False)
    monkeypatch.delenv("KNOWWORK_MASTER_KEY_FILE", raising=False)
    with pytest.raises(ValidationError):
        await worker.run(asyncio.Event())
