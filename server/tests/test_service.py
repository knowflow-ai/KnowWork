"""会话服务：建会话失败不留孤儿、发送与回答提问自动恢复且并发安全、恢复时先补发令牌。"""

import asyncio

import httpx
import pytest
from sqlalchemy import func, select

from knowwork_server.conversations.listeners import Broadcaster
from knowwork_server.conversations.models import Conversation
from knowwork_server.conversations.pump import EventPump, PumpTimings
from knowwork_server.conversations.replay import Cursor, EventFeed
from knowwork_server.conversations.service import ConversationService
from knowwork_server.conversations.writer import BatchWriter
from knowwork_server.kernel.contract import (
    KernelError,
    QuestionAnswer,
    QuestionAnswerItem,
    SessionBusy,
    SessionNotFound,
    TurnInput,
)

from .fakes import FakeKernel

pytestmark = pytest.mark.anyio


def build(kernel, sessionmaker, hook=None):
    writer = BatchWriter(sessionmaker)
    broadcaster = Broadcaster()
    pump = EventPump(kernel, sessionmaker, writer, broadcaster, timings=PumpTimings(idle_timeout=0.2))
    feed = EventFeed(sessionmaker, writer, broadcaster, pump.produced_upto)
    return ConversationService(kernel, sessionmaker, pump, feed, on_host_restart=hook,
                               resume_retry_delays=(0.01, 0.02)), pump


async def count_conversations(sessionmaker, user):
    async with sessionmaker() as session:
        return (await session.execute(select(func.count()).select_from(Conversation)
                                      .where(Conversation.user_id == user))).scalar_one()


@pytest.mark.parametrize("error", [KernelError("session_open_failed", "x", 400), httpx.ConnectError("down"),
                                   asyncio.CancelledError()])
async def test_failed_host_open_leaves_no_orphan(sessionmaker, error):
    kernel = FakeKernel()
    kernel.open_effects = [error]
    service, pump = build(kernel, sessionmaker)
    user = f"u-{id(error)}"
    with pytest.raises(type(error)):
        await service.create(workspace_id="default", user_id=user)
    assert await count_conversations(sessionmaker, user) == 0
    await pump.close()


async def test_send_resumes_lost_session_and_rotates_first(sessionmaker, conversation_id):
    order = []
    kernel = FakeKernel()
    kernel.send_effects = [SessionNotFound("session_not_found", "x", 404)]

    async def hook():
        order.append(("rotate", len(kernel.opened)))

    service, pump = build(kernel, sessionmaker, hook)
    conversation = await service.get(conversation_id=conversation_id, workspace_id="default", user_id="dev-user")
    assert await service.send(conversation, TurnInput(text="hi")) == "m1"
    assert [s.resume for s in kernel.opened] == [True]
    assert order == [("rotate", 1)], "恢复会话后、发送前应先补发模型令牌"
    await pump.close()


class RestartedHost(FakeKernel):
    """宿主刚重启：会话不在，打开一次之后才在；重复打开返回 busy。"""

    def __init__(self):
        super().__init__()
        self.open = False
        self.busy = 0

    async def send(self, session_id, turn):
        await asyncio.sleep(0)
        if not self.open:
            raise SessionNotFound("session_not_found", "x", 404)
        return await super().send(session_id, turn)

    async def open_session(self, spec):
        await asyncio.sleep(0.01)
        if self.open:
            self.busy += 1
            raise SessionBusy("session_busy", "x", 409)
        self.open = True
        return await super().open_session(spec)


async def test_concurrent_sends_resume_once(sessionmaker, conversation_id):
    kernel = RestartedHost()
    service, pump = build(kernel, sessionmaker)
    conversation = await service.get(conversation_id=conversation_id, workspace_id="default", user_id="dev-user")
    results = await asyncio.gather(*(service.send(conversation, TurnInput(text=f"t{i}")) for i in range(5)))
    assert sorted(results) == [f"m{i}" for i in range(1, 6)]
    assert len(kernel.opened) == 1 and kernel.busy == 0, "同一会话的恢复应串行，只打开一次"
    await pump.close()


async def test_resume_tolerates_busy_and_retries(sessionmaker, conversation_id):
    kernel = FakeKernel()
    lost = SessionNotFound("session_not_found", "x", 404)
    kernel.send_effects = [lost, lost]
    kernel.open_effects = [SessionBusy("session_busy", "x", 409)]
    service, pump = build(kernel, sessionmaker)
    conversation = await service.get(conversation_id=conversation_id, workspace_id="default", user_id="dev-user")
    assert await service.send(conversation, TurnInput(text="hi")) == "m1"
    await pump.close()


async def test_events_ensure_the_pump(sessionmaker, conversation_id):
    service, pump = build(FakeKernel(), sessionmaker)
    conversation = await service.get(conversation_id=conversation_id, workspace_id="default", user_id="dev-user")
    assert not pump.is_active(conversation_id)
    service.events(conversation, Cursor())
    assert pump.is_active(conversation_id)
    await pump.close()


ANSWER = QuestionAnswer(answers=(QuestionAnswerItem(id="fmt", selected=("PDF",)),))


async def test_answer_goes_to_the_host_and_touches_the_conversation(sessionmaker, conversation_id):
    kernel = FakeKernel()
    service, pump = build(kernel, sessionmaker)
    conversation = await service.get(conversation_id=conversation_id, workspace_id="default", user_id="dev-user")
    await service.answer(conversation, "call_1", ANSWER)
    assert kernel.answered == [(conversation_id, "call_1", ANSWER)]
    assert kernel.opened == [], "宿主上会话还在时不重开（重开会取消正在等待的提问）"
    after = await service.get(conversation_id=conversation_id, workspace_id="default", user_id="dev-user")
    assert after.updated_at >= conversation.updated_at
    assert pump.is_active(conversation_id)
    await pump.close()


async def test_answer_resumes_lost_session_then_answers(sessionmaker, conversation_id):
    order = []
    kernel = FakeKernel()
    kernel.answer_effects = [SessionNotFound("session_not_found", "x", 404)]

    async def hook():
        order.append(("rotate", len(kernel.opened)))

    service, pump = build(kernel, sessionmaker, hook)
    conversation = await service.get(conversation_id=conversation_id, workspace_id="default", user_id="dev-user")
    await service.answer(conversation, "call_1", ANSWER)
    assert [s.resume for s in kernel.opened] == [True]
    assert order == [("rotate", 1)]
    assert [a[1] for a in kernel.answered] == ["call_1"]
    await pump.close()


async def test_answer_rejected_by_host_is_not_retried(sessionmaker, conversation_id):
    kernel = FakeKernel()
    kernel.answer_effects = [KernelError("question_not_found", "x", 404)]
    service, pump = build(kernel, sessionmaker)
    conversation = await service.get(conversation_id=conversation_id, workspace_id="default", user_id="dev-user")
    with pytest.raises(KernelError):
        await service.answer(conversation, "call_1", ANSWER)
    assert kernel.opened == [] and kernel.answered == []
    await pump.close()
