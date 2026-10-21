"""SSE 的数据来源：先回放（库 + 未提交帧），再接实时推送；以 (seq, 条目下标) 去重。

一帧可能投影出多条 events.v1 事件。SSE id 用 `seq`（这一帧的最后一条）或 `seq.i`（中间的第 i 条），
浏览器带 Last-Event-ID 重连时能精确接上，不漏也不重。

衔接的正确性（序号在推送前分配、落库在后）：
1. 先登记监听者，同一循环片里取未提交帧快照与泵已分配的最大序号 bound；
2. 库里读 (起点, bound]，与快照按序号归并——这个区间里的每一帧要么在快照时已提交，要么在快照里；
3. 大于 bound 的帧一定是登记之后产生的，会从监听者收到；按 (seq, 下标) 去重。
泵在本进程还没恢复位置时（bound 为空），本进程尚未产生过帧，快照为空，库里不设上界即可。
"""

import math
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from .listeners import Broadcaster, StoredEvent
from .models import RunEvent
from .writer import BatchWriter

PAGE_SIZE = 500


@dataclass(frozen=True, order=True)
class Cursor:
    """客户端已收到的位置。index 为 inf 表示这一帧的全部事件都已收到。"""

    seq: int = 0
    index: float = math.inf


def parse_cursor(last_event_id: str | None, after: int = 0) -> Cursor:
    """Last-Event-ID 优先；格式不对就退回 after。不会抛异常。"""
    if last_event_id:
        head, _, tail = last_event_id.strip().partition(".")
        if head.isdecimal() and (not tail or tail.isdecimal()) and len(last_event_id) <= 40:
            try:
                return Cursor(int(head), int(tail) if tail else math.inf)
            except ValueError:
                pass
    return Cursor(max(after, 0))


@dataclass(frozen=True)
class Outgoing:
    sse_id: str
    type: str
    payload: dict


def _expand(seq: int, ui: list | tuple, cursor: Cursor) -> list[Outgoing]:
    out = []
    last = len(ui) - 1
    for index, event in enumerate(ui):
        if Cursor(seq, index) <= cursor or not isinstance(event, dict):
            continue
        sse_id = str(seq) if index == last else f"{seq}.{index}"
        out.append(Outgoing(sse_id=sse_id, type=str(event.get("type", "")), payload=event))
    return out


class EventFeed:
    def __init__(self, sessionmaker: async_sessionmaker, writer: BatchWriter, broadcaster: Broadcaster,
                 produced_upto: Callable[[str], int | None], *, page_size: int = PAGE_SIZE) -> None:
        self._sessionmaker = sessionmaker
        self._writer = writer
        self._broadcaster = broadcaster
        self._produced_upto = produced_upto
        self._page_size = page_size

    async def iterate(self, conversation_id: str, cursor: Cursor) -> AsyncIterator[Outgoing]:
        async with self._broadcaster.listen(conversation_id) as listener:
            from_seq = cursor.seq - 1 if cursor.index != math.inf else cursor.seq
            pending = self._writer.pending(conversation_id, from_seq)
            bound = self._produced_upto(conversation_id)
            position = cursor
            async for seq, ui in self._replay(conversation_id, from_seq, bound, pending):
                for item in _expand(seq, ui, position):
                    yield item
                position = max(position, Cursor(seq))
            while True:
                batch = await listener.next_batch()
                if batch is None:
                    return  # 跟不上被踢出：结束本次 SSE，浏览器带 Last-Event-ID 重连
                for event in batch:
                    for item in _expand(event.seq, event.ui, position):
                        yield item
                    position = max(position, Cursor(event.seq))

    async def _replay(self, conversation_id: str, from_seq: int, bound: int | None,
                      pending: list[StoredEvent]) -> AsyncIterator[tuple[int, list | tuple]]:
        queued = [p for p in pending if bound is None or p.seq <= bound]
        cursor_seq, i = from_seq, 0
        while True:
            rows = await self._page(conversation_id, cursor_seq, bound)
            for seq, ui in rows:
                while i < len(queued) and queued[i].seq < seq:
                    yield queued[i].seq, queued[i].ui
                    i += 1
                if i < len(queued) and queued[i].seq == seq:
                    i += 1
                yield seq, ui or []
                cursor_seq = seq
            if len(rows) < self._page_size:
                break
        for event in queued[i:]:
            if event.seq > cursor_seq:
                yield event.seq, event.ui

    async def _page(self, conversation_id: str, after: int, bound: int | None) -> list[tuple[int, list]]:
        statement = (select(RunEvent.seq, RunEvent.ui)
                     .where(RunEvent.conversation_id == conversation_id, RunEvent.seq > after))
        if bound is not None:
            statement = statement.where(RunEvent.seq <= bound)
        statement = statement.order_by(RunEvent.seq).limit(self._page_size)
        async with self._sessionmaker() as session:
            return [(row.seq, row.ui) for row in (await session.execute(statement)).all()]
