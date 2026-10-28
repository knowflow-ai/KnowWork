"""运行日志的只读访问：按会话取宿主原始帧（run_event.data），给运行调试视图用；界面回放走 replay.EventFeed。

落库是批量异步的，正在跑的回合可能还差最后几帧没写进来，调试视图按「库里已有的」展示。
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from .models import RunEvent

PAGE_SIZE = 500
MAX_EVENTS = 20_000


@dataclass(frozen=True)
class RawEvent:
    seq: int
    kind: str
    data: Any
    at: datetime | None


@dataclass(frozen=True)
class RunLogPage:
    events: tuple[RawEvent, ...]
    truncated: bool  # 超过 MAX_EVENTS 帧，只取了前面这些


def _turn(data: Any) -> int | None:
    inner = data.get("data") if isinstance(data, dict) else None
    value = inner.get("turn") if isinstance(inner, dict) else None
    return value if type(value) is int else None


class RunLog:
    def __init__(self, sessionmaker: async_sessionmaker, *, page_size: int = PAGE_SIZE,
                 max_events: int = MAX_EVENTS) -> None:
        self._sessionmaker = sessionmaker
        self._page_size = page_size
        self._max_events = max_events

    async def read(self, conversation_id: str) -> RunLogPage:
        return await self._read(conversation_id, after=-1, upto=None)

    async def turn_events(self, conversation_id: str, turn: int) -> RunLogPage:
        """某一轮从 turn/start 到 turn/end 的帧（含两端），回合结束后的投影用；库里没有这一轮就是空的。

        宿主重启后轮号会重来，同一个轮号取最后一次开始的那一轮。按 JSON 字段筛选、没有专门索引，
        但只在这一个会话的行里找。
        """
        def mark(kind: str):
            return (RunEvent.conversation_id == conversation_id, RunEvent.kind == "session/event",
                    RunEvent.data["type"].astext == kind, RunEvent.data[("data", "turn")].astext == str(turn))

        async with self._sessionmaker() as session:
            start = await session.scalar(select(func.max(RunEvent.seq)).where(*mark("turn/start")))
            end = None if start is None else await session.scalar(
                select(func.min(RunEvent.seq)).where(*mark("turn/end"), RunEvent.seq > start))
        if start is None:
            return RunLogPage(events=(), truncated=False)
        return await self._read(conversation_id, after=start - 1, upto=end)

    async def _read(self, conversation_id: str, *, after: int, upto: int | None) -> RunLogPage:
        events: list[RawEvent] = []
        while len(events) < self._max_events:
            statement = (select(RunEvent.seq, RunEvent.kind, RunEvent.data, RunEvent.created_at)
                         .where(RunEvent.conversation_id == conversation_id, RunEvent.seq > after)
                         .order_by(RunEvent.seq).limit(self._page_size))
            if upto is not None:
                statement = statement.where(RunEvent.seq <= upto)
            async with self._sessionmaker() as session:
                rows = (await session.execute(statement)).all()
            events.extend(RawEvent(seq=row.seq, kind=row.kind, data=row.data, at=row.created_at) for row in rows)
            if len(rows) < self._page_size:
                return RunLogPage(events=tuple(events), truncated=False)
            after = rows[-1].seq
        return RunLogPage(events=tuple(events[:self._max_events]), truncated=True)
