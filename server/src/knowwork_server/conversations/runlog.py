"""运行日志的只读访问：按会话取宿主原始帧（run_event.data），给运行调试视图用；界面回放走 replay.EventFeed。

落库是批量异步的，正在跑的回合可能还差最后几帧没写进来，调试视图按「库里已有的」展示。
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from .models import Conversation, RunEvent

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


@dataclass(frozen=True)
class TurnMark:
    """某一轮开始或结束的那一帧（管理端「运行记录」列表用）。"""

    conversation_id: str
    turn: int
    at: datetime
    title: str = ""
    user_id: str = ""
    reason: str | None = None  # 结束的原因（completed、cancelled……），开始的那一帧为空


def _turn(data: Any) -> int | None:
    inner = data.get("data") if isinstance(data, dict) else None
    value = inner.get("turn") if isinstance(inner, dict) else None
    return value if type(value) is int else None


def _reason(data: Any) -> str | None:
    inner = data.get("data") if isinstance(data, dict) else None
    reason = inner.get("reason") if isinstance(inner, dict) else None
    kind = reason.get("kind") if isinstance(reason, dict) else None
    return kind if isinstance(kind, str) else None


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

    async def turn_starts(self, workspace_id: str, *, since: datetime, until: datetime, before: datetime | None = None,
                          user_id: str | None = None, limit: int = 50) -> tuple[TurnMark, ...]:
        """时间段内开始的轮次，新的在前，带上会话标题与所属用户。按 JSON 字段筛选、没有专门索引，量大了再加。"""
        statement = (select(RunEvent.conversation_id, RunEvent.data, RunEvent.created_at, Conversation.title,
                            Conversation.user_id)
                     .join(Conversation, Conversation.id == RunEvent.conversation_id)
                     .where(RunEvent.workspace_id == workspace_id, RunEvent.kind == "session/event",
                            RunEvent.data["type"].astext == "turn/start",
                            RunEvent.created_at >= since, RunEvent.created_at < until))
        if before is not None:
            statement = statement.where(RunEvent.created_at < before)
        if user_id is not None:
            statement = statement.where(Conversation.user_id == user_id)
        statement = statement.order_by(RunEvent.created_at.desc()).limit(limit)
        async with self._sessionmaker() as session:
            rows = (await session.execute(statement)).all()
        return tuple(TurnMark(conversation_id=row.conversation_id, turn=turn, at=row.created_at, title=row.title or "",
                              user_id=row.user_id) for row in rows if (turn := _turn(row.data)) is not None)

    async def turn_ends(self, workspace_id: str, conversation_ids: tuple[str, ...]) -> tuple[TurnMark, ...]:
        if not conversation_ids:
            return ()
        statement = (select(RunEvent.conversation_id, RunEvent.data, RunEvent.created_at)
                     .where(RunEvent.workspace_id == workspace_id, RunEvent.conversation_id.in_(conversation_ids),
                            RunEvent.kind == "session/event", RunEvent.data["type"].astext == "turn/end"))
        async with self._sessionmaker() as session:
            rows = (await session.execute(statement)).all()
        return tuple(TurnMark(conversation_id=row.conversation_id, turn=turn, at=row.created_at,
                              reason=_reason(row.data)) for row in rows if (turn := _turn(row.data)) is not None)
