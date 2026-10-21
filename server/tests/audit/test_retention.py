"""保留期清理：按批删除过期记录，逐个工作空间过行级安全，单个空间失败不影响其他空间。"""

import logging
from datetime import UTC, datetime, timedelta

import pytest

from knowwork_server.audit import retention
from knowwork_server.audit.events import AuditEvent
from knowwork_server.audit.retention import purge_expired
from knowwork_server.audit.writer import AuditWriter

from .conftest import count_rows

pytestmark = pytest.mark.anyio
NOW = datetime(2026, 10, 19, 3, 0, tzinfo=UTC)


async def seed(sessionmaker, plan: dict[str, tuple[int, int]]) -> None:
    """plan: 工作空间 -> (过期条数, 未过期条数)。"""
    writer = AuditWriter(sessionmaker)
    writer.start()
    for workspace_id, (old, fresh) in plan.items():
        for i in range(old + fresh):
            age = timedelta(days=91 + i) if i < old else timedelta(days=89)
            writer.put(AuditEvent(workspace_id=workspace_id, actor_type="system", action="seed", result="success",
                                  occurred_at=NOW - age))
    await writer.flush()
    await writer.close()


async def test_purges_expired_rows_in_batches_per_workspace(db, app_sessionmaker, access):
    await seed(app_sessionmaker, {"ws1": (5, 2), "ws2": (3, 1)})
    report = await purge_expired(app_sessionmaker, batch_size=2, now=lambda: NOW)
    assert report.deleted == {"ws1": 5, "ws2": 3} and report.total == 8
    assert report.cutoff == NOW - timedelta(days=90)
    assert await count_rows(db) == 3


async def test_retention_days_is_configurable_and_explicit_workspaces(db, app_sessionmaker):
    await seed(app_sessionmaker, {"ws1": (2, 2), "ws2": (2, 0)})
    report = await purge_expired(app_sessionmaker, retention_days=30, workspace_ids=["ws1"], now=lambda: NOW)
    assert report.deleted == {"ws1": 4}
    assert await count_rows(db, "workspace_id = 'ws2'") == 2


@pytest.mark.parametrize("days,batch", [(0, 10), (-1, 10), (90, 0), (90, 100_001)])
async def test_invalid_parameters_are_rejected(app_sessionmaker, days, batch):
    with pytest.raises(ValueError):
        await purge_expired(app_sessionmaker, retention_days=days, batch_size=batch)


async def test_one_failing_workspace_does_not_stop_the_others(db, app_sessionmaker, monkeypatch, caplog):
    await seed(app_sessionmaker, {"ws1": (1, 0), "ws2": (1, 0)})
    real = retention.purge_workspace

    async def flaky(sessionmaker, workspace_id, cutoff, *, batch_size):
        if workspace_id == "ws1":
            raise RuntimeError("lock timeout")
        return await real(sessionmaker, workspace_id, cutoff, batch_size=batch_size)

    monkeypatch.setattr(retention, "purge_workspace", flaky)
    with caplog.at_level(logging.ERROR):
        report = await purge_expired(app_sessionmaker, workspace_ids=["ws1", "ws2"], now=lambda: NOW)
    assert report.deleted == {"ws2": 1} and "audit purge failed for workspace ws1" in caplog.text
    assert await count_rows(db) == 1
