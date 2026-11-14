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
    def __init__(self, kernel, sessionmaker, timings=FAST, thinking_visible=None):
        self.kernel = kernel
        self.sessionmaker = sessionmaker
        self.writer = BatchWriter(sessionmaker, retry_delays=(0.01,))
        self.broadcaster = Broadcaster()
        self.restarts = 0

        async def restarted():
            self.restarts += 1

        self.pump = EventPump(kernel, sessionmaker, self.writer, self.broadcaster, on_host_restart=restarted,
                              timings=timings, thinking_visible=thinking_visible)

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


async def test_missing_session_releases_the_pump_right_away(sessionmaker, conversation_id):
    """宿主上没有这段对话（空闲释放了）：泵当场退出，不每 0.5 秒去问一次；打开它的一方会再拉起泵。"""
    kernel = FakeKernel([[SessionNotFound("session_not_found", "x", 404)] for _ in range(200)])
    async with Harness(kernel, sessionmaker, PumpTimings(idle_timeout=60, idle_grace=0.1, retry_delays=(0.5,))) as h:
        h.pump.ensure(conversation_id, "default")
        await wait_until(lambda: not h.pump.is_active(conversation_id), timeout=1)
        assert len(kernel.subscribe_calls) == 1, "不该在这里空转"
        h.pump.ensure(conversation_id, "default")
        await wait_until(lambda: not h.pump.is_active(conversation_id), timeout=1)
        assert len(kernel.subscribe_calls) == 2, "再有人要时照常去读"


async def test_someone_asking_during_a_missing_read_reads_again(sessionmaker, conversation_id):
    """读的过程中有人要过泵（发送方刚把会话打开、ensure 时泵还活着没另起）：再读一次，接上打开后的帧。"""
    calls = []

    class Opening(FakeKernel):
        async def subscribe(self, session_id, *, after=0, epoch=None):
            calls.append(after)
            if len(calls) == 1:
                h.pump.ensure(conversation_id, "default")  # 发送方这时打开了会话、要泵接着读
                raise SessionNotFound("session_not_found", "x", 404)
            async for item in super().subscribe(session_id, after=after, epoch=epoch):
                yield item

    kernel = Opening([[session_event(1, "turn/start", {"turn": 1}), HOLD]])
    async with Harness(kernel, sessionmaker) as h:
        h.pump.ensure(conversation_id, "default")
        await wait_until(lambda: h.pump.produced_upto(conversation_id) == 1)
        assert len(calls) == 2 and h.pump.is_active(conversation_id)


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


def live(cursor: int, data: dict) -> object:
    return frame(cursor, "assistant/stream", data)


async def test_live_pieces_are_pushed_but_never_stored_or_numbered(sessionmaker, conversation_id):
    """逐字片段（官方 agent/assistant-stream 的临时帧）：推给正在看的浏览器，不落库、不分配序号、不推进续读位置；
    泵记着这一步已出的字，给中途打开的页面补快照；agent 空闲后清掉。"""
    kernel = FakeKernel([[session_event(1, "turn/start", {"turn": 1}), status(2, "running"),
                          live(2, {"type": "start", "attemptId": "a1", "turn": 1, "step": 1}),
                          live(2, {"type": "text", "attemptId": "a1", "text": "短剧"}),
                          live(2, {"type": "text", "attemptId": "a1", "text": "规模增长"}), HOLD]])
    async with Harness(kernel, sessionmaker) as h:
        async with h.broadcaster.listen(conversation_id) as listener:
            h.pump.ensure(conversation_id, "default")
            await wait_until(lambda: (h.pump.live_snapshot(conversation_id) or {}).get("text") == "短剧规模增长")
            pushed = [e for e in await listener.next_batch() if not hasattr(e, "seq")]
        assert [e.payload.get("text") for e in pushed] == ["", "短剧", "规模增长"]
        assert h.pump.produced_upto(conversation_id) == 2
        rows = await h.rows(conversation_id)
        assert [r.kind for r in rows] == ["session/event", "agent/status"], "片段不落库"

    kernel = FakeKernel([[live(0, {"type": "start", "attemptId": "a1", "turn": 1, "step": 1}),
                          live(0, {"type": "text", "attemptId": "a1", "text": "半截"}), status(1, "idle"), HOLD]])
    async with Harness(kernel, sessionmaker) as h:
        h.pump.ensure(conversation_id, "other")
        await wait_until(lambda: h.pump.produced_upto(conversation_id) is not None
                         and h.pump.produced_upto(conversation_id) >= 3)
        assert h.pump.live_snapshot(conversation_id) is None, "跑完了不再给新打开的页面补半截"



def _thinking_turn() -> list:
    message = {"message": {"content": [{"type": "reasoning", "text": "想一想"}, {"type": "text", "text": "你好"}]}}
    return [session_event(1, "turn/start", {"turn": 1}), status(2, "running"),
            session_event(3, "request/header", {"header": {"tools": [], "config": {"model": "qwen", "provider": "gw"}},
                                                "reason": "initial"}),
            live(3, {"type": "start", "attemptId": "a1", "turn": 1, "step": 1}),
            live(3, {"type": "reasoning", "attemptId": "a1", "text": "想一想"}),
            live(3, {"type": "text", "attemptId": "a1", "text": "你好"}),
            session_event(4, "assistant/message", message), HOLD]


@pytest.mark.parametrize("shown", [True, False])
async def test_thinking_reaches_the_page_only_when_the_model_shows_it(sessionmaker, conversation_id, shown):
    """模型的「显示思考过程」关着：这次请求的思考片段不推、思考记录不进投影，正文照常；原始帧照常落库。"""
    asked: list[str | None] = []

    async def visible(model):
        asked.append(model)
        return shown

    async with Harness(FakeKernel([_thinking_turn()]), sessionmaker, thinking_visible=visible) as h:
        async with h.broadcaster.listen(conversation_id) as listener:
            h.pump.ensure(conversation_id, "default")
            await wait_until(lambda: h.pump.produced_upto(conversation_id) == 4)
            pushed = [e.payload for e in await listener.next_batch() if not hasattr(e, "seq")]
        rows = await h.rows(conversation_id)
    assert asked == ["qwen"]
    assert any(p.get("reasoning") == "想一想" for p in pushed) is shown
    assert any(p.get("text") == "你好" for p in pushed)
    kinds = [u.get("kind") for r in rows for u in r.ui if u["type"] == "item.completed"]
    assert kinds == (["thinking", "message"] if shown else ["message"])
    assert rows[-1].data["data"]["message"]["content"][0]["text"] == "想一想", "原始帧照常落库"


async def test_restarted_pump_recalls_the_model_from_stored_header(sessionmaker, conversation_id):
    """request/header 只在第一次请求与换模型时有：泵重新拉起（业务服务重启）后按库里记下的模型判断，不退回显示。"""
    async def hidden(model):
        return False

    async with Harness(FakeKernel([_thinking_turn()]), sessionmaker, thinking_visible=hidden) as h:
        h.pump.ensure(conversation_id, "default")
        await wait_until(lambda: h.pump.produced_upto(conversation_id) == 4)
        await h.writer.flush()

    asked: list[str | None] = []

    async def visible(model):
        asked.append(model)
        return model != "qwen"

    second_turn = [session_event(5, "turn/start", {"turn": 2}),
                   live(5, {"type": "start", "attemptId": "a2", "turn": 2, "step": 1}),
                   live(5, {"type": "reasoning", "attemptId": "a2", "text": "再想想"}),
                   live(5, {"type": "text", "attemptId": "a2", "text": "好的"}),
                   session_event(6, "assistant/message", {"message": {"content": [{"type": "text", "text": "好的"}]}}),
                   HOLD]
    async with (Harness(FakeKernel([second_turn]), sessionmaker, thinking_visible=visible) as h,
                h.broadcaster.listen(conversation_id) as listener):
        h.pump.ensure(conversation_id, "default")
        await wait_until(lambda: h.pump.produced_upto(conversation_id) == 6)
        pushed = [e.payload for e in await listener.next_batch() if not hasattr(e, "seq")]
    assert asked == ["qwen"], "拉起时从库里取这段对话在用的模型"
    assert not any(p.get("reasoning") for p in pushed), "关着显示的模型重启后照样不推思考"
    assert any(p.get("text") == "好的" for p in pushed)


def pushed(cursor: int, type_: str, data=None):
    return session_event(cursor, type_, data, epoch="device")


async def test_pushed_frames_go_through_the_same_pipeline(sessionmaker, conversation_id):
    """会话不在我们的宿主上跑（在用户设备上执行）：帧由外面送进来，编号、投影、落库、推送和拉来的帧一样；
    重传的跳过；这种会话不去宿主拉；审批取自会话日志。"""
    kernel = FakeKernel()
    async with Harness(kernel, sessionmaker) as h:
        listener = h.broadcaster.listen(conversation_id)
        async with listener as heard:
            first = [pushed(0, "turn/start", {"turn": 1}),
                     pushed(1, "approval/asked", {"id": "ap1", "toolName": "bash", "callId": "c1"})]
            assert await h.pump.push(conversation_id, "default", first) == 1
            again = [first[1], pushed(2, "turn/end", {"turn": 1, "reason": {"kind": "completed"}})]
            assert await h.pump.push(conversation_id, "default", again) == 2
            got = await heard.next_batch()
        h.pump.ensure(conversation_id, "default")
        rows = await h.rows(conversation_id)
    assert kernel.subscribe_calls == [], "外面送帧的会话不去宿主拉"
    assert [(r.seq, r.host_cursor) for r in rows] == [(1, 0), (2, 1), (3, 2)]
    assert [(u["type"], u["kind"]) for r in rows for u in r.ui] == [
        ("run.started", None), ("item.started", "approval"), ("run.waiting", None), ("run.completed", None)]
    assert got, "落库前就推给了正在看的页面"


async def test_pushing_resumes_where_the_database_left_off(sessionmaker, conversation_id):
    async with Harness(FakeKernel(), sessionmaker) as h:
        assert await h.pump.pushed_upto(conversation_id, "default") == {}
        await h.pump.push(conversation_id, "default", [pushed(0, "turn/start", {"turn": 1}), pushed(1, "x/y")])
    async with Harness(FakeKernel(), sessionmaker) as h:  # 业务服务重启过
        assert await h.pump.pushed_upto(conversation_id, "default") == {"device": 1}
        assert await h.pump.push(conversation_id, "default", [pushed(1, "x/y"), pushed(2, "x/z")]) == 2
        rows = await h.rows(conversation_id)
    assert [(r.seq, r.host_cursor) for r in rows] == [(1, 0), (2, 1), (3, 2)]


async def test_each_pushed_source_keeps_its_own_position(sessionmaker, conversation_id):
    """一段对话的帧可以来自几个来源（根会话、各个子 Agent 的会话），各有各的序号：互不当成重传，重启后各自接上。"""
    child = [session_event(0, "turn/start", {"turn": 1}, epoch="device:kid"),
             session_event(1, "turn/end", {"turn": 1}, epoch="device:kid")]
    async with Harness(FakeKernel(), sessionmaker) as h:
        await h.pump.push(conversation_id, "default", [pushed(0, "turn/start", {"turn": 1}), pushed(1, "x/y")])
        assert await h.pump.push(conversation_id, "default", child) == 1, "子会话的 0、1 不是根会话的重传"
        assert await h.pump.push(conversation_id, "default", child) == 1
    async with Harness(FakeKernel(), sessionmaker) as h:
        assert await h.pump.pushed_upto(conversation_id, "default") == {"device": 1, "device:kid": 1}
        rows = await h.rows(conversation_id)
    assert [(r.seq, r.host_epoch, r.host_cursor) for r in rows] == [
        (1, "device", 0), (2, "device", 1), (3, "device:kid", 0), (4, "device:kid", 1)]
