"""逐字输出：DSH 官方 agent/assistant-stream 经宿主以临时帧 assistant/stream 转来，不落库，只推给正在看的浏览器。

泵为每个会话记住正在输出的那一步已经出了哪些字（LiveText）：浏览器中途打开或断线重连时先补一份快照，
之后按片段接着拼。这一步的正式消息照常落成会话事件，前端用它整段替换；结束帧到了这里就清掉。

推给浏览器的内容（SSE 事件名 live，不带 id，不影响 Last-Event-ID 续传）::

    {"run_id": "run-<轮次>", "step": <步>, "offset": <这段之前已有的字数>, "text": <这段>}
    {"run_id": ..., "step": ..., "reasoning_offset": <这段之前已有的思考字数>, "reasoning": <这段思考>}
    {"run_id": ..., "step": ..., "tool": <工具名>}
    {"run_id": ..., "step": ..., "done": true}

offset 为 0 表示从头替换（新一步开始、快照）；不等于前端已有的字数说明中间漏了，前端不拼，等正式消息。
字数按 UTF-16 码元算，和浏览器里字符串的 length 一致：按 Python 的字符数算的话，emoji 这类基本平面以外的字
在浏览器里占两个，一出现两边就差一位，这一步后面的字全不拼了，要等正式消息整段出来（2026-11-10 发现）。
开始与快照总带着 reasoning（开始时为空），表示连思考一起替换；正文片段不带它，前端照旧保留这一步的思考。
tool 是模型开始写某个工具调用了（宿主每个调用只报一次名字，参数不转）：模型把整个文件写进参数时要好十几秒，
页面据此写「正在写文件」，不是一直「思考中」。快照里带上这一步已经报过的工具（tools）。
reasoning 是思考片段（照 DSH 官方聊天界面边想边显示），和正文分开计字数、拼法相同；快照里带上到目前为止的思考全文。
"""

import re
from dataclasses import dataclass, replace

MAX_LIVE_CHARS = 200_000  # 一步最多记这么多字：再长前端也只是等正式消息，不让内存无限涨
MAX_LIVE_TOOLS = 20  # 一步里报过的工具名最多记这么多（一步并发调的工具不会更多）
TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class LiveText:
    attempt: str
    turn: int
    step: int
    text: str = ""
    full: bool = False  # 超过上限后这一步不再记也不再推（漏一段再接着拼会拼出缺字的文字）
    tools: tuple[str, ...] = ()  # 这一步模型开始写了哪些工具调用（按出现先后，去重）
    reasoning: str = ""  # 这一步到目前为止的思考
    reasoning_full: bool = False  # 思考超过上限后不再记也不再推，正文照常
    units: int = 0  # text 的 UTF-16 码元数（offset 按它给，见模块说明）
    reasoning_units: int = 0  # reasoning 的 UTF-16 码元数

    def head(self) -> dict:
        return {"run_id": f"run-{self.turn}", "step": self.step}


@dataclass(frozen=True)
class LiveEvent:
    conversation_id: str
    payload: dict


def advance(current: LiveText | None, data: object) -> tuple[LiveText | None, dict | None]:
    """一帧临时帧 → 新的状态与要推给浏览器的内容（None 表示不推）。认不出的帧不改状态。"""
    if not isinstance(data, dict):
        return current, None
    kind, attempt = data.get("type"), data.get("attemptId")
    if kind == "start":
        turn, step = data.get("turn"), data.get("step")
        if not (isinstance(attempt, str) and attempt and _is_int(turn) and _is_int(step)):
            return current, None
        started = LiveText(attempt=attempt, turn=turn, step=step)
        # 带上空的思考：前端把它当成连思考一起从头替换（正文第一段的 offset 也是 0，但不带 reasoning，不能清掉思考）
        return started, {**started.head(), "offset": 0, "text": "", "reasoning": ""}
    if current is None or attempt != current.attempt:
        return current, None
    if kind == "text":
        text = data.get("text")
        if not isinstance(text, str) or not text or current.full:
            return current, None
        if len(current.text) + len(text) > MAX_LIVE_CHARS:
            return replace(current, full=True), None
        return (replace(current, text=current.text + text, units=current.units + _units(text)),
                {**current.head(), "offset": current.units, "text": text})
    if kind == "reasoning":
        text = data.get("text")
        if not isinstance(text, str) or not text or current.reasoning_full:
            return current, None
        if len(current.reasoning) + len(text) > MAX_LIVE_CHARS:
            return replace(current, reasoning_full=True), None
        return (replace(current, reasoning=current.reasoning + text,
                        reasoning_units=current.reasoning_units + _units(text)),
                {**current.head(), "reasoning_offset": current.reasoning_units, "reasoning": text})
    if kind == "tool":
        name = data.get("name")
        if not isinstance(name, str) or not TOOL_NAME.fullmatch(name) or name in current.tools \
                or len(current.tools) >= MAX_LIVE_TOOLS:
            return current, None
        return replace(current, tools=(*current.tools, name)), {**current.head(), "tool": name}
    if kind == "end":
        return None, {**current.head(), "done": True}
    return current, None


def snapshot(current: LiveText | None) -> dict | None:
    """正在输出的那一步到目前为止的全文（从头替换）、思考与报过的工具；没有在输出时为 None。"""
    if current is None:
        return None
    tools = {"tools": list(current.tools)} if current.tools else {}
    return {**current.head(), "offset": 0, "text": current.text, **tools, "reasoning": current.reasoning}


def _units(text: str) -> int:
    """UTF-16 码元数。surrogatepass：宿主把一个 emoji 的两半拆在两段里时，单独的半个也按一个码元算，和浏览器一样。"""
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0
