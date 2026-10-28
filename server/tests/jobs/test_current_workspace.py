"""当前空间（db.base.current_workspace）：按空间的会话工厂据它声明空间，行级安全照常生效（普通账号连库）。

- 批量写入一批里有几个空间的会话：按空间分开提交，各自只在自己的空间里看得到；
- 后台任务的处理函数以任务所在的空间读写；
- 进出 in_workspace 恢复原样，没有当前空间时什么都不声明。
"""

import pytest
from sqlalchemy import select

from knowwork_server.conversations.listeners import StoredEvent
from knowwork_server.conversations.models import RunEvent
from knowwork_server.conversations.writer import BatchWriter, PendingRow
from knowwork_server.db.base import current_workspace, in_workspace, make_workspace_sessionmaker
from knowwork_server.jobs import JobRunner

from .support import KIND, Clock, put

pytestmark = pytest.mark.anyio


def scoped_of(files_db):
    return make_workspace_sessionmaker(files_db.sessionmaker.kw["bind"])


def pending(conversation, seq=1):
    event = StoredEvent(conversation_id=conversation.id, seq=seq, kind="agent.message", data={}, ui=())
    return PendingRow(event=event, workspace_id=conversation.workspace_id, host_epoch="e", host_cursor=seq)


async def visible_events(scoped, workspace_id):
    with in_workspace(workspace_id):
        async with scoped() as session:
            return {row.conversation_id for row in (await session.scalars(select(RunEvent))).all()}


async def test_one_batch_across_workspaces_lands_in_each(files_db):
    first = await files_db.conversation(workspace_id="ws1")
    second = await files_db.conversation(workspace_id="ws2")
    scoped = scoped_of(files_db)
    writer = BatchWriter(scoped, retry_delays=(0.01,))
    writer.start()
    await writer.put(pending(first))
    await writer.put(pending(second))
    await writer.flush()
    await writer.close()
    assert await visible_events(scoped, "ws1") == {first.id}
    assert await visible_events(scoped, "ws2") == {second.id}


async def test_job_handlers_run_in_their_jobs_workspace(files_db):
    seen = []

    async def handler(ctx):
        seen.append((ctx.claim.workspace_id, current_workspace.get()))

    runner = JobRunner(files_db.sessionmaker, clock=Clock())
    runner.register(KIND, handler)
    for workspace_id in ("ws1", "ws2"):
        await put(files_db, workspace_id=workspace_id)
        assert (await runner.run_once(workspace_id)).status == "succeeded"
    assert seen == [("ws1", "ws1"), ("ws2", "ws2")]
    assert current_workspace.get() is None, "处理函数在自己的协程里设，不漏到执行器外面"


def test_in_workspace_restores_and_nests():
    assert current_workspace.get() is None
    with in_workspace("ws1"):
        with in_workspace("ws2"):
            assert current_workspace.get() == "ws2"
        assert current_workspace.get() == "ws1"
    assert current_workspace.get() is None
    with pytest.raises(ValueError), in_workspace(""):
        pass
