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


# 目标那一步里，这些事件说明模型已经开始输出：输入到此为止
_OUTPUT = frozenset({"assistant/attempt", "assistant/message", "tool/call", "step/end"})


def step_input(events: Sequence[Event], turn: int, step: int) -> StepInput:
    """events 是主会话从头开始的全部事件；收到目标那一步模型的输出为止。

    DSH 0.2.2 起一步的系统提示、提问、宿主追加的上下文、工具清单都记在 step/start 之后（之前的版本记在前面），
    所以不能停在 step/start。排进收件箱的提问先记一次 agent/inbox/spliced、进会话时再记一次同 id 的 user/message，
    按 id 只算一条。系统提示每个请求系列开头整套重发，按发它的那一步分套，取最近的一套。
    """
    system: list[str] = []
    system_of: tuple[object, object] | None = None
    tools: list[ToolInfo] = []
    messages: list[MessageView] = []
    seen: set[str] = set()
    compacted = inside = False

    def add(raw: object) -> None:
        message_id = raw.get("id") if isinstance(raw, dict) else None
        if isinstance(message_id, str) and message_id in seen:
            return
        if isinstance(message_id, str):
            seen.add(message_id)
        found = _message(raw)
        messages.extend((found,) if found else ())

    def result() -> StepInput:
        notes = (APPROXIMATE, COMPACTED) if compacted else (APPROXIMATE,)
        return StepInput(turn=turn, step=step, found=True, system=tuple(system), tools=tuple(tools),
                         messages=tuple(messages), notes=notes)

    for event in events:
        payload = event.payload
        if event.type == "step/start" and turn_of(event) == turn and payload.get("step") == step:
            inside = True
            continue
        if inside and (event.type in _OUTPUT or event.type == "step/start"):
            return result()
        if "compact" in event.type:
            compacted = True
        if event.type == "request/header":
            tools = _tools(payload.get("header"))
        elif event.type == "system/message":
            text = text_of(payload.get("message"))
            series = (payload.get("turn"), payload.get("step"))
            if series != system_of:
                system, system_of = [], series
            if text:
                system.append(text)
        elif event.type == "user/message":
            add(payload)
        elif event.type == "agent/inbox/spliced":
            for raw in payload.get("inserted") or ():
                add(raw)
        elif event.type in ("assistant/message", "tool/result"):
            add(payload.get("message"))
    if inside:  # 这一步模型还没输出（还在跑）：给到目前为止的
        return result()
    return StepInput(turn=turn, step=step, found=False, system=(), tools=(), messages=(), notes=())
