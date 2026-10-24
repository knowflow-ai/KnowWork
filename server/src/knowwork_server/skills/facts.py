"""技能运行事实的投影，纯函数：从一轮的会话日志算出这一轮每个技能的运行与步骤。

只用已经落库的东西，会话进行中不多写一行：
- 宿主帧 skill/loaded：哪个会话、以什么方式加载了哪个技能的哪个版本（manual 是用户 /技能名，automatic 是模型自己加载）；
- 加载之后同一会话里的工具调用、结果与审批；
- 宿主帧 skill-gate/failed：技能的完成判据没满足；
- turn/end 的结束原因，用量表里这一轮各步的 token 与费用，本轮收下的产物数。

一个技能在一轮里算一次运行，同一会话重复加载只算第一次；运行的步骤是加载之后该会话里的每一次工具调用。
同一轮加载了两个技能时，加载之后的工具调用两边都算：日志里分不清某一步是为哪个技能做的，宁可重复也不猜。
用量表只记主会话的模型调用，子 Agent 里加载的技能 token 与费用记 0。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from ..debugview.events import Event, TurnSlice
from ..knowledge.tools import READ_NAME, SEARCH_NAME

SKILL_TOOL = "skill"  # 官方加载技能的工具：调用它属于「选择」，不算技能自己的步骤
KNOWLEDGE_TOOLS = frozenset({SEARCH_NAME, READ_NAME})
NAME_LIMIT = 64
_APPROVED = frozenset({"allowed-once", "allowed-session"})
_REJECTED = frozenset({"rejected", "denied"})  # 审批超时宿主也按 rejected 回
_CANCELLED = frozenset({"aborted", "interrupted"})
_MODES = frozenset({"manual", "automatic"})


class StepUsageLike(Protocol):
    turn: int
    step: int
    input_tokens: int
    output_tokens: int
    cost: Decimal | None


@dataclass(frozen=True)
class StepFact:
    tool_name: str
    status: str  # succeeded | failed | skipped（调用发出去了、回合结束前没等到结果）
    duration_ms: int
    approval_result: str | None  # approved | rejected；没走审批为空


@dataclass(frozen=True)
class RunFact:
    skill: str
    version: str
    session_id: str
    selection_mode: str
    started_ms: int | None
    ended_ms: int | None
    status: str  # succeeded | failed | cancelled
    total_tokens: int
    cost: Decimal
    artifact_count: int
    failure_stage: str | None
    failure_code: str | None
    steps: tuple[StepFact, ...]


@dataclass(frozen=True)
class _Load:
    seq: int
    time_ms: int | None
    skill: str
    version: str
    session_id: str
    mode: str


@dataclass(frozen=True)
class _Ending:
    reason: str
    error_code: str | None
    time_ms: int | None
    gate_missing: frozenset[str]  # 技能门没放行的技能名


def project_turn(turn: TurnSlice, usage: Sequence[StepUsageLike], artifact_count: int) -> tuple[RunFact, ...]:
    """一轮里每个被加载的技能一条运行。没收到 turn/end 的轮次（宿主重启等）不投影，等它真正结束。"""
    end = next((e for e in turn.events if e.session_id is None and e.type == "turn/end"), None)
    loads = _loads(turn.events)
    if end is None or not loads:
        return ()
    ending = _ending(turn.events, end)
    children = frozenset(e.session_id for e in turn.events if e.session_id is not None)
    turn_usage = tuple(record for record in usage if record.turn == turn.turn)
    return tuple(_run(load, turn.events, load.session_id in children, turn_usage, artifact_count, ending)
                 for load in loads)


def _text(value: Any, limit: int = NAME_LIMIT) -> str:
    return value[:limit] if isinstance(value, str) else ""


def _loads(events: Sequence[Event]) -> tuple[_Load, ...]:
    seen: set[tuple[str, str]] = set()
    loads: list[_Load] = []
    for event in events:
        if event.type != "skill/loaded":
            continue
        payload = event.payload
        skill = _text(payload.get("skill"))
        version = _text(payload.get("version"))
        session_id = _text(payload.get("sessionId"), 128)
        if not (skill and version and session_id) or (session_id, skill) in seen:
            continue
        seen.add((session_id, skill))
        mode = payload.get("mode") if payload.get("mode") in _MODES else "automatic"
        loads.append(_Load(seq=event.seq, time_ms=event.time_ms, skill=skill, version=version,
                           session_id=session_id, mode=mode))
    return tuple(loads)


def _ending(events: Sequence[Event], end: Event) -> _Ending:
    reason = end.payload.get("reason") if isinstance(end.payload.get("reason"), dict) else {}
    error = reason.get("error") if isinstance(reason.get("error"), dict) else {}
    missing = frozenset(
        _text(item.get("skill"))
        for event in events if event.type == "skill-gate/failed"
        for item in (event.payload.get("missing") if isinstance(event.payload.get("missing"), list) else ())
        if isinstance(item, dict) and isinstance(item.get("skill"), str))
    return _Ending(reason=_text(reason.get("kind")) or "completed", error_code=_text(error.get("code")) or None,
                   time_ms=end.time_ms, gate_missing=missing)


def _run(load: _Load, events: Sequence[Event], in_child: bool, usage: Sequence[StepUsageLike],
         artifact_count: int, ending: _Ending) -> RunFact:
    own = tuple(e for e in events if e.seq > load.seq and e.session_id == (load.session_id if in_child else None))
    steps = _steps(own)
    presented = any(e.type == "deliverables/presented" and e.session_id is None for e in events if e.seq > load.seq)
    artifacts = artifact_count if presented else 0
    records = () if in_child else _usage_since(events, load, usage)
    status, stage, code = _verdict(steps, ending, gated=not in_child and load.skill in ending.gate_missing,
                                   presented=presented, artifacts=artifacts)
    return RunFact(skill=load.skill, version=load.version, session_id=load.session_id, selection_mode=load.mode,
                   started_ms=load.time_ms, ended_ms=ending.time_ms, status=status,
                   total_tokens=sum(r.input_tokens + r.output_tokens for r in records),
                   cost=sum((r.cost for r in records if r.cost is not None), Decimal(0)), artifact_count=artifacts,
                   failure_stage=stage, failure_code=code, steps=steps)


def _steps(events: Sequence[Event]) -> tuple[StepFact, ...]:
    calls: dict[str, dict[str, Any]] = {}
    approvals: dict[str, str] = {}  # 审批 id → 调用 id
    for event in events:
        payload = event.payload
        if event.type == "tool/call" and isinstance(payload.get("callId"), str):
            if payload.get("name") != SKILL_TOOL:
                calls[payload["callId"]] = {"name": _text(payload.get("name")), "started": event.time_ms,
                                            "ended": None, "error": None, "approval": None}
        elif event.type == "tool/result":
            message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
            call = calls.get(str(message.get("toolCallId")))
            if call is not None:
                call.update(ended=event.time_ms, error=message.get("isError") is True)
        elif event.type == "approval/asked" and isinstance(payload.get("callId"), str):
            approvals[str(payload.get("id"))] = payload["callId"]
        elif event.type == "approval/decided":
            call = calls.get(approvals.get(str(payload.get("id")), ""))
            outcome = payload.get("outcome")
            if call is not None:
                call["approval"] = "approved" if outcome in _APPROVED else "rejected" if outcome in _REJECTED else None
    return tuple(_step(call) for call in calls.values())


def _step(call: dict[str, Any]) -> StepFact:
    status = "skipped" if call["error"] is None else "failed" if call["error"] else "succeeded"
    started, ended = call["started"], call["ended"]
    duration = ended - started if started is not None and ended is not None and ended >= started else 0
    return StepFact(tool_name=call["name"], status=status, duration_ms=duration, approval_result=call["approval"])


def _usage_since(events: Sequence[Event], load: _Load, usage: Sequence[StepUsageLike]) -> tuple[StepUsageLike, ...]:
    """主会话从加载那一步起（含：模型决定加载也是为这个技能花的）的用量；加载早于第一步（用户 /技能名）就是整轮。"""
    started = [e.payload.get("step") for e in events
               if e.session_id is None and e.type == "step/start" and e.seq < load.seq]
    first = started[-1] if started and type(started[-1]) is int else None
    return tuple(r for r in usage if first is None or r.step >= first)


def _verdict(steps: Sequence[StepFact], ending: _Ending, *, gated: bool, presented: bool,
             artifacts: int) -> tuple[str, str | None, str | None]:
    """终态与失败阶段。阶段只用日志里看得到的证据判，看不出来的归到最接近的那一类，不猜原因。"""
    if ending.reason in _CANCELLED:
        return "cancelled", None, None
    if ending.reason == "error":
        return "failed", "generation", ending.error_code or "model_error"
    if ending.reason == "max-tokens":
        return "failed", "generation", "max_tokens"
    if ending.reason == "blocked":  # 请求被策略拦下，没发给模型
        return "failed", "permission", "blocked"
    if gated:
        return "failed", _blame(steps) or "step", "completion_missing"
    if presented and artifacts == 0:  # 交付过文件，却一个都没收下（超限、读不出来等）
        return "failed", "delivery", "artifact_missing"
    return "succeeded", None, None


def _blame(steps: Sequence[StepFact]) -> str | None:
    """技能没完成时，最后一个出错的步骤说明卡在哪：审批被拒是权限，知识库工具出错是知识，其余工具出错是工具。"""
    for step in reversed(steps):
        if step.approval_result == "rejected":
            return "permission"
        if step.status == "failed":
            return "knowledge" if step.tool_name in KNOWLEDGE_TOOLS else "tool"
    return None
