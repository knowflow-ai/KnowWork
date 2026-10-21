"""会话日志的规整与按轮切分（运行调试视图的第一步），纯函数。

run_event.data 原样保存宿主帧：
- kind = session/event 时是 DSH 官方会话事件 {seq, type, time, data}；
- kind = subagent/event 时是 {sessionId, event}，event 是子 Agent 会话自己的同一套事件；
- 其余 kind（question/requested、skill/loaded 等）是宿主自己的帧，没有 time，用落库时间。
轮次以主会话的 turn/start、turn/end 为界；两轮之间到达的事件（例如用户新发的消息先进收件箱）算下一轮的。
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from ..conversations.runlog import RawEvent

SESSION = "session/event"
SUBAGENT = "subagent/event"


@dataclass(frozen=True)
class Event:
    """规整后的一条事件：session_id 为 None 表示主会话。"""

    seq: int
    type: str
    time_ms: int | None
    payload: dict[str, Any]
    session_id: str | None = None


def _ms(raw: RawEvent) -> int | None:
    return int(raw.at.timestamp() * 1000) if raw.at is not None else None


def normalize(raw: RawEvent) -> Event | None:
    data = raw.data if isinstance(raw.data, dict) else {}
    if raw.kind in (SESSION, SUBAGENT):
        inner = data if raw.kind == SESSION else data.get("event")
        if not isinstance(inner, dict) or not isinstance(inner.get("type"), str):
            return None
        payload = inner.get("data") if isinstance(inner.get("data"), dict) else {}
        time = inner.get("time")
        session_id = data.get("sessionId") if raw.kind == SUBAGENT else None
        return Event(seq=raw.seq, type=inner["type"], time_ms=time if isinstance(time, int) else _ms(raw),
                     payload=payload, session_id=session_id if isinstance(session_id, str) else None)
    return Event(seq=raw.seq, type=raw.kind, time_ms=_ms(raw), payload=data)


def turn_of(event: Event) -> int | None:
    value = event.payload.get("turn")
    return value if type(value) is int else None


@dataclass(frozen=True)
class TurnSlice:
    turn: int
    events: tuple[Event, ...]
    raw: tuple[RawEvent, ...]


def slice_turns(raws: Iterable[RawEvent]) -> tuple[TurnSlice, ...]:
    """按主会话的轮次切分。还没开始的那一轮（只有排队的输入）不出现。"""
    slices: list[TurnSlice] = []
    pending: list[tuple[RawEvent, Event]] = []
    current: int | None = None
    for raw in raws:
        event = normalize(raw)
        if event is None:
            continue
        main = event.session_id is None
        if main and event.type == "turn/start" and turn_of(event) is not None:
            if current is not None:  # 上一轮没收到 turn/end（宿主重启等）：就此结束
                slices.append(_slice(current, pending))
                pending = []
            current = turn_of(event)
        pending.append((raw, event))
        if main and event.type == "turn/end" and current is not None:
            slices.append(_slice(current, pending))
            pending, current = [], None
    if current is not None:
        slices.append(_slice(current, pending))
    return tuple(slices)


def _slice(turn: int, items: Sequence[tuple[RawEvent, Event]]) -> TurnSlice:
    return TurnSlice(turn=turn, events=tuple(e for _, e in items), raw=tuple(r for r, _ in items))


def main_events(raws: Iterable[RawEvent]) -> tuple[Event, ...]:
    """主会话的全部事件（还原某一步的输入时要从头看）。"""
    events = (normalize(raw) for raw in raws)
    return tuple(e for e in events if e is not None and e.session_id is None)
