"""会话删除时经 ConversationService 的 erasers 清掉附件与产物（与主线装配方式一致）。"""

import pytest

from knowwork_server.artifacts import build_artifacts
from knowwork_server.attachments import AttachmentSettings, IncomingFile, build_attachments
from knowwork_server.conversations.listeners import Broadcaster
from knowwork_server.conversations.pump import EventPump
from knowwork_server.conversations.replay import EventFeed
from knowwork_server.conversations.service import ConversationService
from knowwork_server.conversations.writer import BatchWriter
from knowwork_server.sessionfiles import local_session_stores

from ..fake_parsers import EnvParsers
from ..fake_sandbox import FakeSandbox
from ..fakes import FakeKernel

pytestmark = pytest.mark.anyio


async def test_conversation_delete_runs_both_erasers(files_db, tmp_path):
    sessionmaker, sandbox, stores = files_db.sessionmaker, FakeSandbox(), local_session_stores(tmp_path)
    attachments = build_attachments(sessionmaker, stores, sandbox, AttachmentSettings(),
                                    parsers=EnvParsers(AttachmentSettings()))
    artifacts = build_artifacts(sessionmaker, stores, sandbox)
    conv = await files_db.conversation(workspace_id="default", user_id="dev-user")
    await attachments.service.upload(conv, [IncomingFile("a.txt", b"in")])
    sandbox.write_output("default", conv.id, "b.txt", b"out")
    await artifacts.collector.collect("default", conv.id)

    from knowwork_server.db.base import in_workspace, make_workspace_sessionmaker
    scoped = make_workspace_sessionmaker(sessionmaker.kw["bind"])
    kernel, writer, broadcaster = FakeKernel(), BatchWriter(scoped), Broadcaster()
    pump = EventPump(kernel, scoped, writer, broadcaster)
    service = ConversationService(kernel, scoped, pump, EventFeed(scoped, writer, broadcaster, pump.produced_upto),
                                  erasers=(attachments.service.erase, artifacts.service.erase))
    with in_workspace("default"):
        await service.delete(conv)
    await pump.close()
    assert await attachments.service.list(conv) == () and await artifacts.service.list(conv) == ()
    assert not any((tmp_path / "default" / conv.id).rglob("*.*"))
