"""批量落库与 SSE 回放衔接：存不下的帧变占位、库不可用时不丢、回放与未提交帧归并、慢消费者被关闭。"""

import asyncio
import math

import pytest
from sqlalchemy import select

from knowwork_server.conversations.listeners import Broadcaster, StoredEvent
from knowwork_server.conversations.models import RunEvent
from knowwork_server.conversations.replay import Cursor, EventFeed, parse_cursor
from knowwork_server.conversations.writer import BatchWriter, PendingRow

pytestmark = pytest.mark.anyio


def ui(seq, n=1):
    return tuple({"schema_version": "events.v1", "type": f"t{seq}.{i}", "seq": seq} for i in range(n))


def row(cid, seq, *, data=None, n=1):
    event = StoredEvent(conversation_id=cid, seq=seq, kind="session/event", data=data or {"n": seq}, ui=ui(seq, n))
    return PendingRow(event=event, workspace_id="default", host_epoch="e1", host_cursor=seq)


async def stored(sessionmaker, cid):
    async with sessionmaker() as session:
        return (await session.execute(select(RunEvent).where(RunEvent.conversation_id == cid)
                                      .order_by(RunEvent.seq))).scalars().all()


async def test_unstorable_row_becomes_placeholder_with_same_seq(sessionmaker, conversation_id):
    writer = BatchWriter(sessionmaker, retry_delays=(0.01,))
    writer.start()
    for r in (row(conversation_id, 1), row(conversation_id, 2, data={"bad": "\x00"}), row(conversation_id, 3)):
        await writer.put(r)
    await writer.close()
    rows = await stored(sessionmaker, conversation_id)
    assert [(r.seq, r.kind) for r in rows] == [(1, "session/event"), (2, "kernel.invalid"), (3, "session/event")]
    assert rows[1].data["reason"] == "unstorable" and rows[1].ui == list(ui(2))


async def test_rows_without_conversation_are_dropped_not_stuck(sessionmaker, conversation_id):
    writer = BatchWriter(sessionmaker, retry_delays=(0.01,))
    writer.start()
    await writer.put(row("c_missing", 1))
    await writer.put(row(conversation_id, 1))
    await writer.close(timeout=3)
    assert writer.pending(conversation_id) == [] and writer.pending("c_missing") == []
    assert len(await stored(sessionmaker, conversation_id)) == 1


async def test_database_outage_keeps_rows_pending_until_it_recovers(sessionmaker, conversation_id):
    failures = {"left": 3}

    class Flaky:
        def __call__(self):
            if failures["left"] > 0:
                failures["left"] -= 1
                raise ConnectionError("database down")
            return sessionmaker()

    writer = BatchWriter(Flaky(), retry_delays=(0.01,))
    writer.start()
    await writer.put(row(conversation_id, 1))
    await asyncio.sleep(0)
    assert [e.seq for e in writer.pending(conversation_id)] == [1]
    await writer.close(timeout=3)
    assert [r.seq for r in await stored(sessionmaker, conversation_id)] == [1]


async def test_put_waits_when_pending_is_full(sessionmaker, conversation_id):
    writer = BatchWriter(sessionmaker, max_pending=2)  # 不启动后台任务：什么都不会被提交
    await writer.put(row(conversation_id, 1))
    await writer.put(row(conversation_id, 2))
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.1):
            await writer.put(row(conversation_id, 3))


@pytest.mark.parametrize(("header", "after", "expected"), [
    (None, 0, Cursor(0)), (None, 7, Cursor(7)), (None, -5, Cursor(0)),
    ("12", 0, Cursor(12)), ("12.0", 0, Cursor(12, 0)), ("abc", 3, Cursor(3)), ("1.2.3", 0, Cursor(0)),
    ("-1", 0, Cursor(0)), ("9" * 100, 0, Cursor(0)), ("١٢", 0, Cursor(12)), (" 4 ", 0, Cursor(4)),
])
def test_parse_cursor_never_raises(header, after, expected):
    assert parse_cursor(header, after) == expected


async def collect(feed, cid, cursor, count, timeout=3.0):
    out = []
    async with asyncio.timeout(timeout):
        async for item in feed.iterate(cid, cursor):
            out.append(item)
            if len(out) == count:
                break
    return out


async def test_replay_merges_committed_and_pending_rows(sessionmaker, conversation_id):
    committed = BatchWriter(sessionmaker)
    committed.start()
    for seq in (1, 3, 5):
        await committed.put(row(conversation_id, seq))
    await committed.close()
    pending = BatchWriter(sessionmaker)  # 未启动：2、4、6 一直停在待写缓冲里
    for seq in (2, 4, 6):
        await pending.put(row(conversation_id, seq, n=2 if seq == 4 else 1))
    feed = EventFeed(sessionmaker, pending, Broadcaster(), lambda cid: 6, page_size=2)
    items = await collect(feed, conversation_id, Cursor(0), 7)
    assert [i.sse_id for i in items] == ["1", "2", "3", "4.0", "4", "5", "6"]


async def test_resume_from_middle_of_a_frame_is_exact(sessionmaker, conversation_id):
    writer = BatchWriter(sessionmaker)
    for seq in (1, 2):
        await writer.put(row(conversation_id, seq, n=3))
    feed = EventFeed(sessionmaker, writer, Broadcaster(), lambda cid: 2)
    items = await collect(feed, conversation_id, parse_cursor("1.1"), 4)
    assert [i.sse_id for i in items] == ["1", "2.0", "2.1", "2"]


async def test_live_events_after_replay_are_deduplicated(sessionmaker, conversation_id):
    writer = BatchWriter(sessionmaker)
    broadcaster = Broadcaster()
    await writer.put(row(conversation_id, 1))
    feed = EventFeed(sessionmaker, writer, broadcaster, lambda cid: 1)

    async def publish_later():
        await asyncio.sleep(0.05)
        broadcaster.publish(row(conversation_id, 1).event)  # 重复
        broadcaster.publish(row(conversation_id, 2).event)

    task = asyncio.create_task(publish_later())
    items = await collect(feed, conversation_id, Cursor(0), 2)
    await task
    assert [i.sse_id for i in items] == ["1", "2"]


async def test_slow_consumer_is_closed_and_stream_ends(sessionmaker, conversation_id):
    writer = BatchWriter(sessionmaker)
    broadcaster = Broadcaster(backlog=3)
    feed = EventFeed(sessionmaker, writer, broadcaster, lambda cid: 0)
    iterator = feed.iterate(conversation_id, Cursor(0))
    first = asyncio.create_task(anext(iterator))
    await asyncio.sleep(0.05)  # 监听者已登记，消费方此后不再读
    for seq in range(1, 10):
        broadcaster.publish(row(conversation_id, seq).event)
    assert broadcaster.count(conversation_id) == 0, "跟不上的监听者应被踢出且集合被清理"
    with pytest.raises(StopAsyncIteration):
        async with asyncio.timeout(1):
            await first


async def test_listener_set_is_cleaned_up_after_disconnect(sessionmaker, conversation_id):
    broadcaster = Broadcaster()
    feed = EventFeed(sessionmaker, BatchWriter(sessionmaker), broadcaster, lambda cid: 0)
    iterator = feed.iterate(conversation_id, Cursor(0))
    task = asyncio.create_task(anext(iterator))
    await asyncio.sleep(0.05)
    assert broadcaster.count(conversation_id) == 1
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await iterator.aclose()
    assert broadcaster.count(conversation_id) == 0 and conversation_id not in broadcaster._listeners


def test_cursor_order():
    assert Cursor(3, 0) < Cursor(3, 1) < Cursor(3) < Cursor(4, 0)
    assert Cursor(3).index == math.inf
