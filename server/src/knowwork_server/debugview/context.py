"""还原某一步发给模型的输入（运行调试视图）：系统提示、工具清单，加上这一步之前的全部消息，纯函数。

只用会话日志拼，不另存请求原文，所以和模型实际收到的请求可能有出入：上下文压缩、工具结果裁剪等
宿主在发请求前做的改动这里看不到，界面上要说明。要逐字准确，得在模型网关记原文（以后再做）。
"""

from collections.abc import Sequence
from dataclasses import dataclass

from .blocks import Block, blocks_of, clip, source_of, text_of
from .events import Event, turn_of

DESCRIPTION_LIMIT = 400
APPROXIMATE = "按会话日志还原；宿主发请求前做的上下文压缩、工具结果裁剪在这里看不到，和模型实际收到的可能有出入"
COMPACTED = "这一步之前上下文被压缩过：下面列的是压缩前的原始消息，模型实际看到的是压缩后的摘要"


@dataclass(frozen=True)
class ToolInfo:
    name: str
    description: str


@dataclass(frozen=True)
class MessageView:
    role: str
    blocks: tuple[Block, ...]
    source: str | None = None  # 见 blocks.source_of：区分用户亲手发的与宿主塞进来的上下文
    tool_call_id: str | None = None
    is_error: bool = False


@dataclass(frozen=True)
class StepInput:
    turn: int
    step: int
    found: bool
    system: tuple[str, ...]
    tools: tuple[ToolInfo, ...]
    messages: tuple[MessageView, ...]
    notes: tuple[str, ...]


def _tools(header: object) -> list[ToolInfo]:
    tools = header.get("tools") if isinstance(header, dict) else None
    return [ToolInfo(name=str(t.get("name") or ""), description=clip(str(t.get("description") or ""),
                                                                      DESCRIPTION_LIMIT)[0])
            for t in tools or () if isinstance(t, dict)]


def _message(raw: object) -> MessageView | None:
    if not isinstance(raw, dict):
        return None
    return MessageView(role=str(raw.get("role") or "unknown"), blocks=blocks_of(raw), source=source_of(raw),
                       tool_call_id=raw.get("toolCallId") if isinstance(raw.get("toolCallId"), str) else None,
                       is_error=raw.get("isError") is True)


def step_input(events: Sequence[Event], turn: int, step: int) -> StepInput:
    """events 是主会话从头开始的全部事件；遇到目标那一步的 step/start 为止。"""
    system: list[str] = []
    tools: list[ToolInfo] = []
    messages: list[MessageView] = []
    compacted = False
    for event in events:
        payload = event.payload
        if event.type == "step/start" and turn_of(event) == turn and payload.get("step") == step:
            notes = (APPROXIMATE, COMPACTED) if compacted else (APPROXIMATE,)
            return StepInput(turn=turn, step=step, found=True, system=tuple(system), tools=tuple(tools),
                             messages=tuple(messages), notes=notes)
        if "compact" in event.type:
            compacted = True
        if event.type == "request/header":
            if payload.get("startsSeries") is True:
                system = []
            tools = _tools(payload.get("header"))
        elif event.type == "system/message":
            text = text_of(payload.get("message"))
            if text:
                system.append(text)
        elif event.type == "user/message":
            found = _message(payload)
            messages.extend((found,) if found else ())
        elif event.type == "agent/inbox/spliced":
            inserted = (_message(m) for m in payload.get("inserted") or ())
            messages.extend(m for m in inserted if m is not None)
        elif event.type in ("assistant/message", "tool/result"):
            found = _message(payload.get("message"))
            messages.extend((found,) if found else ())
    return StepInput(turn=turn, step=step, found=False, system=(), tools=(), messages=(), notes=())
