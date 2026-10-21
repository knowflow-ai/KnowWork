"""批量写入器与 sink：按工作空间过行级安全写库、队列满不阻塞、数据库故障时退避重试、坏行丢弃不卡住队列。"""

import asyncio
import logging

import pytest
from sqlalchemy import select

from knowwork_server.access.rls import set_workspace
from knowwork_server.audit.events import AuditEvent
from knowwork_server.audit.models import AuditLog
from knowwork_server.audit.sink import DatabaseAuditSink, FanoutSink, NullSink, safe_record
from knowwork_server.audit.writer import AuditWriter

from .conftest import count_rows

pytestmark = pytest.mark.anyio


def event(workspace_id="ws1", **overrides) -> AuditEvent:
    return AuditEvent(**{"workspace_id": workspace_id, "actor_type": "human", "action": "member.add",
                         "result": "success", **overrides})


async def test_writes_events_of_several_workspaces_through_row_level_security(db, app_sessionmaker):
    writer = AuditWriter(app_sessionmaker)
    writer.start()
    for i in range(5):
        assert writer.put(event("ws1" if i % 2 else "ws2", resource_id=f"r{i}", detail={"password": "x", "n": i}))
    await writer.flush()
    assert writer.backlog == 0
    await writer.close()
    assert await count_rows(db) == 5
    async with app_sessionmaker() as session:
        await set_workspace(session, "ws1")
        rows = (await session.execute(select(AuditLog).order_by(AuditLog.resource_id))).scalars().all()
    assert [r.resource_id for r in rows] == ["r1", "r3"]  # 行级安全只放出 ws1 的
    assert all("password" not in r.detail for r in rows)


async def test_full_queue_drops_without_blocking(app_sessionmaker, caplog):
    writer = AuditWriter(app_sessionmaker, max_pending=2)  # 不启动：队列只进不出
    assert writer.put(event()) and writer.put(event())
    with caplog.at_level(logging.ERROR):
        assert writer.put(event()) is False
    assert writer.dropped == 1 and "audit queue full" in caplog.text


class Flaky:
    """前 n 次打开会话就失败，模拟数据库暂时不可用。"""

    def __init__(self, real, failures: int) -> None:
        self.real, self.failures, self.calls = real, failures, 0

    def __call__(self):
        self.calls += 1
        if self.calls <= self.failures:
            raise ConnectionError("db down")
        return self.real()


async def test_retries_until_the_database_is_back(db, app_sessionmaker):
    flaky = Flaky(app_sessionmaker, failures=4)
    writer = AuditWriter(flaky, retry_delays=(0.01,))
    writer.start()
    writer.put(event())
    await writer.flush(timeout=5)
    await writer.close()
    assert flaky.calls > 4
    assert await count_rows(db) == 1


async def test_unstorable_row_is_dropped_and_the_rest_is_written(db, app_sessionmaker, caplog):
    writer = AuditWriter(app_sessionmaker, retry_delays=(0.01,))
    writer.start()
    writer.put(event(resource_id="bad\x00id"))  # PostgreSQL 文本不能含 NUL
    writer.put(event(resource_id="good"))
    with caplog.at_level(logging.ERROR):
        await asyncio.wait_for(writer.flush(timeout=5), timeout=6)
    await writer.close()
    assert writer.backlog == 0
    assert await count_rows(db) == 1
    assert await count_rows(db, "resource_id = 'good'") == 1
    assert "unstorable" in caplog.text


async def test_close_reports_unwritten_events(app_sessionmaker, caplog):
    writer = AuditWriter(app_sessionmaker)
    writer.put(event())
    with caplog.at_level(logging.ERROR):
        await writer.close(timeout=0.05)
    assert "closing with 1 unwritten" in caplog.text


class Boom:
    async def record(self, event):
        raise RuntimeError("外发失败")


async def test_safe_record_logs_sink_failures(caplog, memory_sink):
    with caplog.at_level(logging.ERROR):
        await safe_record(Boom(), event())
    assert "audit sink failed" in caplog.text
    await safe_record(None, event())  # 没配 sink 时什么都不做
    await FanoutSink([Boom(), memory_sink, NullSink()]).record(event(action="grant.create"))
    assert [e.action for e in memory_sink.events] == ["grant.create"]


async def test_database_sink_enqueues(app_sessionmaker):
    writer = AuditWriter(app_sessionmaker)
    await DatabaseAuditSink(writer).record(event())
    assert writer.backlog == 1
