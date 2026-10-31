"""这一轮随提问带上的背景（运行调试视图），纯函数：扩展每轮补的上下文（conversations/turn_context.py，例如记忆），
宿主的 turn-context 插件把它包进 <system-reminder> 追加成用户角色消息，来源标 knowwork-turn-context。

- 常驻那段只在变了、被压缩掉时才发，没发的那几轮模型看到的是之前那一份：往前找到这一轮结束时生效的那一份，
  注明哪一轮发的；清空时宿主发一句「不再适用」（摘要 cleared），之后就没有了；
- 本轮那段（例如按本轮提问召回的几条）只看这一轮追加的；
- 只看主会话：宿主只给根会话补上下文。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from .blocks import text_of
from .events import Event, TurnSlice

KIND = "knowwork-turn-context"  # 同 runtime-host turn-context.mjs
CLEARED = "cleared"
OPEN, CLOSE = "<system-reminder>", "</system-reminder>"


@dataclass(frozen=True)
class Background:
    kind: Literal["standing", "turn"]
    text: str
    sent_turn: int  # 哪一轮发的：常驻那段沿用时早于这一轮


def background(turns: Sequence[TurnSlice], turn: int) -> tuple[Background, ...]:
    standing: Background | None = None
    this_turn: list[Background] = []
    for piece in turns:
        if piece.turn > turn:
            break
        for event in piece.events:
            source = _source(event)
            if source is None:
                continue
            if source.get("standing") is True:
                cleared = source.get("digest") == CLEARED
                standing = None if cleared else Background("standing", _unwrap(event), piece.turn)
            elif piece.turn == turn:
                this_turn.append(Background("turn", _unwrap(event), piece.turn))
    return (*([standing] if standing is not None else []), *this_turn)


def _source(event: Event) -> dict | None:
    if event.session_id is not None or event.type != "user/message":
        return None
    source = event.payload.get("source")
    return source if isinstance(source, dict) and source.get("kind") == KIND else None


def _unwrap(event: Event) -> str:
    text = text_of(event.payload).strip()
    if text.startswith(OPEN) and text.endswith(CLOSE):
        text = text[len(OPEN):-len(CLOSE)]
    return text.strip()
