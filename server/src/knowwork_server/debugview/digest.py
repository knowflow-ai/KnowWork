"""把一轮的会话日志整理成步骤时间线（运行调试视图），纯函数：每步的思考、回答、工具调用与结果、
没成功的模型调用与重试，再对上用量表里这一步的 token、费用与耗时。子 Agent 的步骤按子会话分组放在下面。"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from ..conversations.events_v1 import user_parts
from .blocks import blocks_of, clip, source_of, text_of
from .events import Event, TurnSlice, turn_of

REQUEST_LIMIT = 200
_OUTCOMES = {"allowed-once": "批准本次", "allowed-session": "本会话内同类操作都批准", "denied": "拒绝"}


@dataclass(frozen=True)
class StepUsageView:
    model: str
    input_tokens: int
    output_tokens: int
    cost: str | None
    duration_ms: int | None


@dataclass(frozen=True)
class ToolCallView:
    call_id: str
    name: str
    arguments: str
    result: str | None
    is_error: bool
    started_ms: int | None
    duration_ms: int | None
    truncated: bool


@dataclass(frozen=True)
class StepView:
    step: int
    started_ms: int | None
    duration_ms: int | None
    reasoning: str
    text: str
    tool_calls: tuple[ToolCallView, ...]
    failures: tuple[str, ...]
    usage: StepUsageView | None


@dataclass(frozen=True)
class TurnSummary:
    turn: int
    started_ms: int | None
    duration_ms: int | None
    status: str  # completed、cancelled、failed……；还没结束是 running
    request: str
    steps: int
    tool_calls: int
    errors: int
    input_tokens: int
    output_tokens: int
    cost: str | None


@dataclass(frozen=True)
class SubagentView:
    session_id: str
    label: str
    steps: tuple[StepView, ...]


@dataclass(frozen=True)
class TurnDetail:
    summary: TurnSummary
    steps: tuple[StepView, ...]
    subagents: tuple[SubagentView, ...]
    events: tuple[str, ...]  # 技能加载、审批、提问这类不属于某一步的事


UsageKey = tuple[int, int]


def _int(value: Any) -> int | None:
    return value if type(value) is int else None


def _attempt_failure(payload: dict) -> str | None:
    for item in payload.get("stream") or ():
        chunk = item.get("chunk") if isinstance(item, dict) else None
        reason = chunk.get("reason") if isinstance(chunk, dict) and chunk.get("type") == "finish" else None
        failure = reason.get("failure") if isinstance(reason, dict) and reason.get("kind") == "error" else None
        if isinstance(failure, dict):
            return str(failure.get("message") or failure.get("code") or "模型调用失败")
    return None


def build_steps(events: Sequence[Event], usage: Mapping[UsageKey, Any] | None = None) -> tuple[StepView, ...]:
    """一个会话（主会话或某个子 Agent）在这一轮里的步骤，按步号排序。"""
    steps: dict[int, dict[str, Any]] = {}
    calls: dict[str, dict[str, Any]] = {}

    def at(step: int) -> dict[str, Any]:
        return steps.setdefault(step, {"started": None, "ended": None, "reasoning": "", "text": "", "calls": [],
                                       "failures": [], "turn": None})

    for event in events:
        payload, step = event.payload, _int(event.payload.get("step"))
        if step is None:
            continue
        entry = at(step)
        entry["turn"] = entry["turn"] if entry["turn"] is not None else turn_of(event)
        if event.type == "step/start":
            entry["started"] = event.time_ms
        elif event.type == "step/end":
            entry["ended"] = event.time_ms
        elif event.type == "assistant/message":
            message = payload.get("message")
            entry["reasoning"] += text_of(message, "reasoning")
            entry["text"] += text_of(message, "text")
        elif event.type == "assistant/attempt" and (failure := _attempt_failure(payload)):
            entry["failures"].append(f"模型调用失败：{failure}")
        elif event.type == "llm/retry":
            failure = payload.get("failure") if isinstance(payload.get("failure"), dict) else {}
            entry["failures"].append(f"第 {payload.get('retry')} 次重试：{failure.get('message') or failure.get('code')}")
        elif event.type == "tool/call" and isinstance(payload.get("callId"), str):
            call = {"name": str(payload.get("name") or ""), "arguments": str(payload.get("arguments") or ""),
                    "started": event.time_ms, "result": None, "error": False, "ended": None}
            calls[payload["callId"]] = call
            entry["calls"].append(payload["callId"])
        elif event.type == "tool/result":
            message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
            call = calls.get(str(message.get("toolCallId")))
            if call is not None:
                call.update(result="\n".join(b.text for b in blocks_of(message)), error=message.get("isError") is True,
                            ended=event.time_ms)
    return tuple(_step_view(step, steps[step], calls, usage) for step in sorted(steps))


def _step_view(step: int, entry: dict[str, Any], calls: Mapping[str, dict[str, Any]],
               usage: Mapping[UsageKey, Any] | None) -> StepView:
    record = usage.get((entry["turn"], step)) if usage is not None and entry["turn"] is not None else None
    return StepView(step=step, started_ms=entry["started"], duration_ms=_span(entry["started"], entry["ended"]),
                    reasoning=clip(entry["reasoning"])[0], text=clip(entry["text"])[0],
                    tool_calls=tuple(_call_view(call_id, calls[call_id]) for call_id in entry["calls"]),
                    failures=tuple(entry["failures"]), usage=_usage_view(record))


def _call_view(call_id: str, call: dict[str, Any]) -> ToolCallView:
    arguments, cut_args = clip(call["arguments"])
    result, cut_result = clip(call["result"]) if call["result"] is not None else (None, False)
    return ToolCallView(call_id=call_id, name=call["name"], arguments=arguments, result=result, is_error=call["error"],
                        started_ms=call["started"], duration_ms=_span(call["started"], call["ended"]),
                        truncated=cut_args or cut_result)


def _usage_view(record: Any) -> StepUsageView | None:
    if record is None:
        return None
    return StepUsageView(model=record.model_alias, input_tokens=record.input_tokens,
                         output_tokens=record.output_tokens, cost=_money(record.cost), duration_ms=record.duration_ms)


def _span(start: int | None, end: int | None) -> int | None:
    return end - start if start is not None and end is not None and end >= start else None


def _money(value: Decimal | None) -> str | None:
    return None if value is None else format(value.normalize(), "f")


def _typed_by_user(message: Any) -> bool:
    return isinstance(message, dict) and message.get("role") == "user" and source_of(message) in (None, "user")


def _request(events: Sequence[Event]) -> str:
    """这一轮用户亲手发的第一条消息；宿主塞进来的运行上下文、技能内容不算。"""
    for event in events:
        if event.session_id is not None:
            continue
        candidates = (event.payload,) if event.type == "user/message" else (
            tuple(event.payload.get("inserted") or ()) if event.type == "agent/inbox/spliced" else ())
        for message in candidates:
            if _typed_by_user(message):
                return _readable_request(message)
    return ""


def _readable_request(message: dict) -> str:
    """提问原话；宿主给附件写的说明块（沙箱里的路径、读取提示）换成「附件：文件名」，和对话里显示的一致。"""
    content = message.get("content")
    if isinstance(content, list):
        text, attachments = user_parts(content)
        names = "、".join(item["name"] for item in attachments)
    else:
        text, names = text_of(message), ""
    request = " ".join(text.split())
    if names:
        request = f"{request}（附件：{names}）" if request else f"附件：{names}"
    return clip(request, REQUEST_LIMIT)[0]


def summarize(turn: TurnSlice, usage: Mapping[UsageKey, Any]) -> TurnSummary:
    main = [e for e in turn.events if e.session_id is None]
    steps = build_steps(main, usage)
    end = next((e for e in main if e.type == "turn/end"), None)
    start = next((e for e in main if e.type == "turn/start"), None)
    reason = end.payload.get("reason") if end is not None else None
    status = str(reason.get("kind") or "ended") if isinstance(reason, dict) else ("running" if end is None else "ended")
    records = [record for (t, _), record in usage.items() if t == turn.turn]
    costs = [record.cost for record in records if record.cost is not None]
    errors = sum(len(s.failures) for s in steps) + sum(c.is_error for s in steps for c in s.tool_calls)
    return TurnSummary(
        turn=turn.turn, started_ms=start.time_ms if start else None,
        duration_ms=_span(start.time_ms if start else None, end.time_ms if end else None), status=status,
        request=_request(turn.events), steps=len(steps), tool_calls=sum(len(s.tool_calls) for s in steps),
        errors=errors, input_tokens=sum(r.input_tokens for r in records),
        output_tokens=sum(r.output_tokens for r in records), cost=_money(sum(costs, Decimal(0))) if costs else None)


def detail(turn: TurnSlice, usage: Mapping[UsageKey, Any]) -> TurnDetail:
    main = [e for e in turn.events if e.session_id is None]
    labels = {str(e.payload.get("childId")): str(e.payload.get("label") or "") for e in main
              if e.type == "subagent/catalog"}
    children: dict[str, list[Event]] = {}
    for event in turn.events:
        if event.session_id is not None:
            children.setdefault(event.session_id, []).append(event)
    subagents = tuple(SubagentView(session_id=sid, label=labels.get(sid) or sid[:8], steps=build_steps(events))
                      for sid, events in children.items())
    return TurnDetail(summary=summarize(turn, usage), steps=build_steps(main, usage), subagents=subagents,
                      events=_side_events(main))


def _side_events(main: Sequence[Event]) -> tuple[str, ...]:
    asked: dict[str, str] = {}
    notes: list[str] = []
    for event in main:
        payload = event.payload
        if event.type == "skill/loaded":
            notes.append(f"加载技能 {payload.get('skill')} {payload.get('version') or ''}".rstrip())
        elif event.type == "approval/asked":
            asked[str(payload.get("id"))] = str(payload.get("toolName") or "")
        elif event.type == "approval/decided":
            outcome = str(payload.get("outcome"))
            notes.append(f"审批 {asked.get(str(payload.get('id')), '')}：{_OUTCOMES.get(outcome, outcome)}")
        elif event.type == "question/requested":
            notes.append(f"向用户提问 {len(payload.get('questions') or ())} 个问题")
        elif event.type == "question/answered":
            notes.append("用户回答了提问" + ("（超时后才回答）" if payload.get("late") else ""))
    return tuple(notes)
