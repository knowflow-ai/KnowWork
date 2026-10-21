"""事件泵：坏帧不卡死、异常退避而不静默死掉、空闲回收与再拉起、宿主重启通知、gap 单独标记。"""

import asyncio
import logging

import httpx
import pytest
from sqlalchemy import select

from knowwork_server.conversations.listeners import Broadcaster
from knowwork_server.conversations.models import RunEvent
from knowwork_server.conversations.pump import EventPump, PumpTimings
from knowwork_server.conversations.writer import BatchWriter
from knowwork_server.kernel.contract import EpochMismatch, KernelError, SessionNotFound

from .fakes import HOLD, FakeKernel, frame, session_event, status

pytestmark = pytest.mark.anyio

FAST = PumpTimings(idle_timeout=0.4, idle_grace=0.1, retry_delays=(0.01, 0.02))


class Harness:
    def __init__(self, kernel, sessionmaker, timings=FAST):
        self.kernel = kernel
        self.sessionmaker = sessionmaker
        self.writer = BatchWriter(sessionmaker, retry_delays=(0.01,))
        self.broadcaster = Broadcaster()
        self.restarts = 0

        async def restarted():
            self.restarts += 1

        self.pump = EventPump(kernel, sessionmaker, self.writer, self.broadcaster, on_host_restart=restarted,
                              timings=timings)

    async def __aenter__(self):
        self.writer.start()
        return self

    async def __aexit__(self, *exc):
        await self.pump.close()
        await self.writer.close()

    async def rows(self, cid):
        await self.writer.flush()
        async with self.sessionmaker() as session:
            return (await session.execute(select(RunEvent).where(RunEvent.conversation_id == cid)
                                          .order_by(RunEvent.seq))).scalars().all()


async def wait_until(predicate, timeout=5.0):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


async def test_frames_are_numbered_stored_and_projected(sessionmaker, conversation_id):
    kernel = FakeKernel([[session_event(1, "turn/start", {"turn": 1}), status(2, "running"),
                          session_event(3, "turn/end", {"turn": 1, "reason": {"kind": "completed"}}), HOLD]])
    async with Harness(kernel, sessionmaker) as h:
        h.pump.ensure(conversation_id, "default")
        await wait_until(lambda: h.pump.produced_upto(conversation_id) == 3)
        rows = await h.rows(conversation_id)
    assert [(r.seq, r.host_cursor) for r in rows] == [(1, 1), (2, 2), (3, 3)]
    assert [u["type"] for r in rows for u in r.ui] == ["run.started", "run.completed"]
    assert rows[0].data["type"] == "turn/start", "原始帧必须完整落库"


async def test_nul_bytes_and_bad_frames_do_not_stall(sessionmaker, conversation_id):
    kernel = FakeKernel([[frame(1, "x/raw", {"text": "a\x00b", "k\x00": "\ud800"}),
                          frame(2, "kernel.invalid", {"reason": "invalid_frame"}),
                          session_event(3, "turn/start", {"turn": 1}), HOLD]])
    async with Harness(kernel, sessionmaker) as h:
        h.pump.ensure(conversation_id, "default")
        await wait_until(lambda: h.pump.produced_upto(conversation_id) == 3)
        rows = await h.rows(conversation_id)
    assert [r.kind for r in rows] == ["x/raw", "kernel.invalid", "session/event"]
    assert rows[0].data == {"text": "ab", "k": "?"}


async def test_errors_back_off_and_recover_instead_of_dying(sessionmaker, conversation_id, caplog):
    kernel = FakeKernel([
        [KernelError("too_many_subscribers", "x", 429)],
        [KernelError("unauthorized", "x", 401)],
        [httpx.ReadTimeout("slow")],
        [RuntimeError("boom")],
        [session_event(1, "turn/start", {"turn": 1}), HOLD],
    ])
    caplog.set_level(logging.WARNING)
    async with Harness(kernel, sessionmaker) as h:
        h.pump.ensure(conversation_id, "default")
        await wait_until(lambda: h.pump.produced_upto(conversation_id) == 1)
        assert h.pump.is_active(conversation_id)
    assert "boom" in caplog.text or "failed, retry" in caplog.text


async def test_resubscribes_from_last_position(sessionmaker, conversation_id):
    kernel = FakeKernel([[session_event(5, "turn/start", {"turn": 1}), httpx.RemoteProtocolError("cut")],
                         [session_event(6, "user/message"), HOLD]])
    async with Harness(kernel, sessionmaker) as h:
        h.pump.ensure(conversation_id, "default")
        await wait_until(lambda: h.pump.produced_upto(conversation_id) == 2)
    assert kernel.subscribe_calls == [(0, None), (5, "e1")]


async def test_position_is_restored_from_database(sessionmaker, conversation_id):
    first = FakeKernel([[session_event(7, "turn/start", {"turn": 1}), HOLD]])
    async with Harness(first, sessionmaker) as h:
        h.pump.ensure(conversation_id, "default")
        await wait_until(lambda: h.pump.produced_upto(conversation_id) == 1)
    second = FakeKernel([[session_event(8, "user/message"), HOLD]])
    async with Harness(second, sessionmaker) as h:
        h.pump.ensure(conversation_id, "default")
        await wait_until(lambda: h.pump.produced_upto(conversation_id) == 2)
        rows = await h.rows(conversation_id)
    assert second.subscribe_calls[0] == (7, "e1")
    assert [r.seq for r in rows] == [1, 2]


async def test_epoch_change_restarts_from_zero_and_notifies(sessionmaker, conversation_id):
    kernel = FakeKernel([[session_event(3, "turn/start", {"turn": 1}), EpochMismatch("epoch_mismatch", "x", 410)],
                         [session_event(1, "turn/start", {"turn": 2}, epoch="e2"), HOLD]])
    async with Harness(kernel, sessionmaker) as h:
        h.pump.ensure(conversation_id, "default")
        await wait_until(lambda: h.pump.produced_upto(conversation_id) == 2)
        rows = await h.rows(conversation_id)
        assert h.restarts == 1
    assert kernel.subscribe_calls[1] == (0, None)
    assert {(r.host_epoch, r.host_cursor) for r in rows} == {("e1", 3), ("e2", 1)}


async def test_gap_is_stored_separately_with_warning(sessionmaker, conversation_id, caplog):
    kernel = FakeKernel([[frame(9, "gap", {"after": 1, "oldest": 10}), HOLD]])
    caplog.set_level(logging.WARNING)
    async with Harness(kernel, sessionmaker) as h:
        h.pump.ensure(conversation_id, "default")
        await wait_until(lambda: h.pump.produced_upto(conversation_id) == 1)
        rows = await h.rows(conversation_id)
    assert rows[0].kind == "kernel.gap" and rows[0].data == {"after": 1, "oldest": 10}
    assert rows[0].ui[0]["data"]["code"] == "events_gap"
    assert "dropped frames" in caplog.text


async def test_pump_exits_after_idle_and_is_restarted_on_demand(sessionmaker, conversation_id):
    kernel = FakeKernel([[session_event(1, "turn/start", {"turn": 1}), status(2, "running"),
                          session_event(3, "turn/end", {"turn": 1, "reason": {"kind": "completed"}}),
                          status(4, "idle"), HOLD],
                         [status(5, "running"), HOLD]])
    async with Harness(kernel, sessionmaker) as h:
        h.pump.ensure(conversation_id, "default")
        await wait_until(lambda: h.pump.produced_upto(conversation_id) == 4)
        await wait_until(lambda: not h.pump.is_active(conversation_id), timeout=2)
        h.pump.ensure(conversation_id, "default")
        assert h.pump.is_active(conversation_id)
        await wait_until(lambda: h.pump.produced_upto(conversation_id) == 5)
    assert kernel.subscribe_calls == [(0, None), (4, "e1")]


async def test_running_agent_keeps_pump_alive_without_frames(sessionmaker, conversation_id):
    kernel = FakeKernel([[status(1, "running"), HOLD]])
    async with Harness(kernel, sessionmaker) as h:
        h.pump.ensure(conversation_id, "default")
        await wait_until(lambda: h.pump.produced_upto(conversation_id) == 1)
        await asyncio.sleep(FAST.idle_timeout * 2)
        assert h.pump.is_active(conversation_id), "agent 在跑（例如长工具调用）时不能空闲回收"


async def test_demand_during_idle_grace_keeps_pump(sessionmaker, conversation_id):
    kernel = FakeKernel([[status(1, "idle"), HOLD], [status(2, "running"), HOLD]])
    async with Harness(kernel, sessionmaker) as h:
        h.pump.ensure(conversation_id, "default")
        await wait_until(lambda: h.pump.produced_upto(conversation_id) == 1)
        h.pump.ensure(conversation_id, "default")  # 宽限期内有人发送
        await wait_until(lambda: h.pump.produced_upto(conversation_id) == 2)
        assert h.pump.is_active(conversation_id)


async def test_missing_session_eventually_releases_pump(sessionmaker, conversation_id):
    kernel = FakeKernel([[SessionNotFound("session_not_found", "x", 404)] for _ in range(200)])
    async with Harness(kernel, sessionmaker) as h:
        h.pump.ensure(conversation_id, "default")
        await wait_until(lambda: not h.pump.is_active(conversation_id), timeout=3)


async def test_ordinary_exceptions_never_end_the_pump(sessionmaker, conversation_id):
    class Broken(FakeKernel):
        def subscribe(self, *args, **kwargs):
            raise SystemError("订阅直接抛错")

    async with Harness(Broken(), sessionmaker) as h:
        h.pump.ensure(conversation_id, "default")
        await asyncio.sleep(0.1)
        assert h.pump.is_active(conversation_id), "普通异常应退避重试而不是退出"


async def test_done_callback_logs_a_dead_pump(caplog):
    async def die():
        raise RuntimeError("致命错误")

    task = asyncio.create_task(die(), name="event-pump:x")
    with pytest.raises(RuntimeError):
        await task
    caplog.set_level(logging.ERROR)
    EventPump._on_done(task)
    assert "event pump event-pump:x died" in caplog.text


async def test_observer_sees_every_frame_and_its_errors_do_not_stop_the_pump(sessionmaker, conversation_id):
    seen = []

    def observer(cid, workspace_id, kind, data):
        seen.append((cid, workspace_id, kind))
        raise RuntimeError("observer bug")

    kernel = FakeKernel([[session_event(1, "turn/start", {"turn": 1}), status(2, "running"), HOLD]])
    writer, broadcaster = BatchWriter(sessionmaker), Broadcaster()
    writer.start()
    pump = EventPump(kernel, sessionmaker, writer, broadcaster, timings=FAST, observer=observer)
    try:
        pump.ensure(conversation_id, "default")
        await wait_until(lambda: pump.produced_upto(conversation_id) == 2)
    finally:
        await pump.close()
        await writer.close()
    assert seen == [(conversation_id, "default", "session/event"), (conversation_id, "default", "agent/status")]
