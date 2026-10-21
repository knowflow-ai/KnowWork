"""执行事件模型 events.v1：把宿主帧（DSH 原始会话事件 + KnowWork 插件帧）投影成前端只认的事件。

设计见技术方案 4.1、4.2 节。这里只有纯函数：输入当前投影状态与一帧，
输出新状态与零到多条 events.v1 事件。原始帧另行完整落库，投影结果也随帧落库，
所以回放不需要重新计算、也不依赖投影状态。

事件格式（每条都是 dict）::

    {"schema_version": "events.v1", "type": <见 RUN_TYPES / ITEM_TYPES>,
     "seq": <来源帧在会话内的序号>, "run_id": "run-<轮次>" | None, "time": <毫秒>,
     "item_id": str | None, "kind": <条目类型> | None, "data": {...}}

未知帧类型投影为空列表：只落库，不推前端（设计里的 kernel.native）。
"""

import json
import re
from dataclasses import dataclass, replace
from typing import Any

SCHEMA = "events.v1"

RUN_TYPES = ("run.started", "run.completed", "run.failed", "run.cancelled", "run.waiting")
ITEM_TYPES = ("item.started", "item.delta", "item.updated", "item.completed", "item.failed")
ITEM_KINDS = ("message", "thinking", "tool", "approval", "input_request", "todo", "artifact", "evidence",
              "subagent", "background_job", "error")

GAP_KIND = "kernel.gap"
TEXT_LIMIT = 8000
# 宿主给附件写的说明块（runtime-host attachment-handles.mjs，逐字照官方 dsh-llm 的 fileHandleText）：
# 不进显示文字，换成附件列表。文件名是 JSON 字符串（官方 quoted 即 JSON.stringify）。
_FILE_HANDLE = re.compile(r'^\[File (?P<name>"(?:[^"\\]|\\.)*") \((?P<bytes>\d+) bytes, sha256:[0-9a-f]{8}\): '
                          r'verbatim read-only copy saved at ')
_EXTRACTED_HANDLE = "[Markdown extracted from "
FIELD_LIMIT = 2000
LABEL_LIMIT = 1000
MAX_QUESTIONS = 16
MAX_OPTIONS = 32
MAX_TODOS = 100
TODO_STATUSES = ("pending", "in_progress", "completed")
QUESTION_FRAMES = ("question/requested", "question/answered", "question/continued", "question/closed")


@dataclass(frozen=True)
class ProjectionState:
    """跨帧需要记住的最少信息：当前轮次（插件帧不带轮次）、本轮技能门是否已判失败。"""

    turn: int | None = None
    gate_failed_turn: int | None = None


def _run_id(turn: int | None) -> str | None:
    return None if turn is None else f"run-{turn}"


def _event(kind_type: str, *, seq: int, turn: int | None, time: int, item_id: str | None = None,
           kind: str | None = None, data: dict | None = None) -> dict:
    return {"schema_version": SCHEMA, "type": kind_type, "seq": seq, "run_id": _run_id(turn), "time": time,
            "item_id": item_id, "kind": kind, "data": data or {}}


def _int(value: Any) -> int | None:
    return value if type(value) is int else None


def _turn(payload: dict, state: ProjectionState) -> int | None:
    turn = _int(payload.get("turn"))
    return state.turn if turn is None else turn


def _texts(content: Any, block_types: tuple[str, ...]) -> str:
    if not isinstance(content, list):
        return ""
    parts = [b.get("text", "") for b in content
             if isinstance(b, dict) and b.get("type") in block_types and isinstance(b.get("text"), str)]
    return "".join(parts)[:TEXT_LIMIT]


def _assistant_message(state: ProjectionState, payload: dict, seq: int, time: int) -> list[dict]:
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    message_id = str(message.get("id") or f"msg-{seq}")
    turn = _turn(payload, state)
    events = []
    thinking = _texts(message.get("content"), ("reasoning", "thinking"))
    if thinking:
        events.append(_event("item.completed", seq=seq, turn=turn, time=time, item_id=f"{message_id}:thinking",
                             kind="thinking", data={"text": thinking}))
    text = _texts(message.get("content"), ("text",))
    if text:
        events.append(_event("item.completed", seq=seq, turn=turn, time=time, item_id=message_id,
                             kind="message", data={"role": "assistant", "text": text}))
    return events


def _user_message(state: ProjectionState, payload: dict, seq: int, time: int) -> list[dict]:
    """用户亲自发的消息（提问与插话）归到当前这一轮。宿主也用 user/message 注入运行时上下文、
    子 Agent 回报等，它们的 source.kind 不是 user，不展示给用户。"""
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    if source.get("kind") != "user":
        return []
    text, attachments = user_parts(payload.get("content"))
    if not text and not attachments:
        return []
    message_id = str(payload.get("id") or f"user-{seq}")
    data = {"role": "user", "text": text, **({"attachments": attachments} if attachments else {})}
    return [_event("item.completed", seq=seq, turn=state.turn, time=time, item_id=message_id, kind="message",
                   data=data)]


def user_parts(content: Any) -> tuple[str, list[dict]]:
    """用户原话与附件：附件说明块按固定格式认出来，只留文件名与大小（路径是沙箱内部的，不给浏览器）。"""
    texts: list[str] = []
    attachments: list[dict] = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict) or block.get("type") != "text" or not isinstance(block.get("text"), str):
            continue
        found = _FILE_HANDLE.match(block["text"])
        if found is not None:
            try:
                attachments.append({"name": str(json.loads(found["name"])), "size": int(found["bytes"])})
            except ValueError:
                texts.append(block["text"])
        elif not block["text"].startswith(_EXTRACTED_HANDLE):
            texts.append(block["text"])
    return "".join(texts)[:TEXT_LIMIT], attachments


def _turn_end(state: ProjectionState, payload: dict, seq: int, time: int) -> tuple[ProjectionState, list[dict]]:
    turn = _turn(payload, state)
    reason = payload.get("reason") if isinstance(payload.get("reason"), dict) else {}
    kind = reason.get("kind")
    gate_failed = turn is not None and state.gate_failed_turn == turn
    if kind == "aborted":
        event = _event("run.cancelled", seq=seq, turn=turn, time=time)
    elif kind == "error":
        error = reason.get("error") if isinstance(reason.get("error"), dict) else {}
        code = str(error.get("code") or "UNKNOWN")[:64]
        event = _event("run.failed", seq=seq, turn=turn, time=time, data={"code": code, "message": "本轮执行失败"})
    elif kind == "blocked":
        event = _event("run.failed", seq=seq, turn=turn, time=time, data={"code": "blocked", "message": "本轮被拦截"})
    elif gate_failed:
        event = _event("run.failed", seq=seq, turn=turn, time=time,
                       data={"code": "skill_gate_failed", "message": "未满足技能的完成判据"})
    else:
        data = {"reason": "max_tokens"} if kind == "max-tokens" else {}
        event = _event("run.completed", seq=seq, turn=turn, time=time, data=data)
    return replace(state, gate_failed_turn=None), [event]


def _tool_call(state: ProjectionState, payload: dict, seq: int, time: int) -> list[dict]:
    call_id = payload.get("callId")
    if not isinstance(call_id, str):
        return []
    turn = _turn(payload, state)
    return [_event("item.started", seq=seq, turn=turn, time=time, item_id=call_id, kind="tool",
                   data={"name": str(payload.get("name", "")), "arguments": payload.get("arguments")})]


def _tool_result(state: ProjectionState, payload: dict, seq: int, time: int) -> list[dict]:
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    call_id = message.get("toolCallId") or (message.get("source") or {}).get("callId")
    if not isinstance(call_id, str):
        return []
    turn = _turn(payload, state)
    failed = message.get("isError") is True
    data: dict = {"text": _texts(message.get("content"), ("text",))}
    error = payload.get("error")
    if failed and isinstance(error, dict):
        data["code"] = str(error.get("code", ""))[:64]
    return [_event("item.failed" if failed else "item.completed", seq=seq, turn=turn, time=time,
                   item_id=call_id, kind="tool", data=data)]


def _session_event(state: ProjectionState, event: Any, seq: int) -> tuple[ProjectionState, list[dict]]:
    if not isinstance(event, dict):
        return state, []
    kind = event.get("type")
    payload = event.get("data") if isinstance(event.get("data"), dict) else {}
    time = _int(event.get("time")) or 0  # 缺时间时记 0，不影响排序（排序看 seq）
    if kind == "turn/start":
        turn = _int(payload.get("turn"))
        return replace(state, turn=turn, gate_failed_turn=None), [_event("run.started", seq=seq, turn=turn, time=time)]
    if kind == "turn/end":
        return _turn_end(state, payload, seq, time)
    if kind == "assistant/message":
        return state, _assistant_message(state, payload, seq, time)
    if kind == "user/message":
        return state, _user_message(state, payload, seq, time)
    if kind == "tool/call":
        return state, _tool_call(state, payload, seq, time)
    if kind == "tool/result":
        return state, _tool_result(state, payload, seq, time)
    if kind == "todo/write":
        return state, _todos(state, payload, seq, time)
    return state, []


def _todos(state: ProjectionState, payload: dict, seq: int, time: int) -> list[dict]:
    """官方待办是整表快照（每次写入替换整张表），按轮次一个条目：item_id 固定为 todo-<轮次>，后到的覆盖先到的。"""
    raw = payload.get("todos")
    if not isinstance(raw, list):
        return []
    items = [{"content": item["content"][:FIELD_LIMIT], "status": item["status"]} for item in raw[:MAX_TODOS]
             if isinstance(item, dict) and isinstance(item.get("content"), str) and item.get("status") in TODO_STATUSES]
    item_id = "todo" if state.turn is None else f"todo-{state.turn}"
    return [_event("item.updated", seq=seq, turn=state.turn, time=time, item_id=item_id, kind="todo",
                   data={"items": items})]


def _approval(state: ProjectionState, kind: str, data: dict, seq: int, time: int) -> list[dict]:
    approval_id = data.get("id")
    if not isinstance(approval_id, str):
        return []
    if kind == "approval/resolved":
        return [_event("item.completed", seq=seq, turn=state.turn, time=time, item_id=approval_id,
                       kind="approval", data={"outcome": str(data.get("outcome", ""))})]
    detail = {"tool_name": str(data.get("toolName", "")), "call_id": data.get("callId"), "reason": data.get("reason")}
    return [
        _event("item.started", seq=seq, turn=state.turn, time=time, item_id=approval_id, kind="approval", data=detail),
        _event("run.waiting", seq=seq, turn=state.turn, time=time, item_id=approval_id,
               data={"reason": "approval"}),
    ]


def _text(value: Any, limit: int = FIELD_LIMIT) -> str | None:
    return value[:limit] if isinstance(value, str) and value else None


def _options(raw: Any) -> list[dict]:
    """选项标签原样保留（回答要回传原标签，截断了就对不上），过长的整项丢掉。"""
    items = raw if isinstance(raw, list) else []
    options = []
    for option in items[:MAX_OPTIONS]:
        label = option.get("label") if isinstance(option, dict) else None
        if not isinstance(label, str) or not 0 < len(label) <= LABEL_LIMIT:
            continue
        description = _text(option.get("description"))
        options.append({"label": label, **({} if description is None else {"description": description})})
    return options


def _questions(raw: Any) -> list[dict]:
    items = raw if isinstance(raw, list) else []
    questions = []
    for item in items[:MAX_QUESTIONS]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not _text(item.get("question")):
            continue
        questions.append({"id": item["id"][:256], "question": _text(item["question"]), "header": _text(item.get("header")),
                          "detail": _text(item.get("detail")), "options": _options(item.get("options")),
                          "multi_select": item.get("multiSelect") is True})
    return questions


def _answers(raw: Any) -> list[dict]:
    items = raw if isinstance(raw, list) else []
    answers = []
    for item in items[:MAX_QUESTIONS]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        selected = [label for label in (item.get("selected") or [])[:MAX_OPTIONS]
                    if isinstance(label, str) and len(label) <= LABEL_LIMIT]
        custom = _text(item.get("custom"), 4000)
        answers.append({"id": item["id"][:256], "selected": selected, **({} if custom is None else {"custom": custom})})
    return answers


def _question(state: ProjectionState, kind: str, data: dict, seq: int, time: int) -> list[dict]:
    """提问应答者的帧。item_id 是工具调用 id，迟到回答落在后来那一轮里，前端按 item_id 找回原卡片。"""
    question_id = data.get("id")
    if not isinstance(question_id, str):
        return []
    common = {"seq": seq, "turn": state.turn, "time": time, "item_id": question_id, "kind": "input_request"}
    if kind == "question/requested":
        detail = {"questions": _questions(data.get("questions")), "timed": data.get("timed") is True}
        return [_event("item.started", **common, data=detail),
                _event("run.waiting", seq=seq, turn=state.turn, time=time, item_id=question_id, data={"reason": "input"})]
    if kind == "question/answered":
        return [_event("item.completed", **common, data={"answers": _answers(data.get("answers")),
                                                          "late": data.get("late") is True})]
    if kind == "question/continued":
        return [_event("item.updated", **common, data={"state": "continued"})]
    # interrupted：宿主正常关闭时官方把等待中的提问中止了，恢复后不能再答（见宿主 question-broker）
    reason = data.get("reason") if data.get("reason") in ("timeout", "cancelled", "interrupted") else "cancelled"
    return [_event("item.failed", **common, data={"reason": reason})]


def _subagent(state: ProjectionState, kind: str, data: dict, seq: int, time: int) -> list[dict]:
    child = data.get("childSessionId")
    if not isinstance(child, str):
        return []
    if kind == "subagent/started":
        return [_event("item.started", seq=seq, turn=state.turn, time=time, item_id=child, kind="subagent")]
    return [_event("item.completed", seq=seq, turn=state.turn, time=time, item_id=child, kind="subagent",
                   data={"status": str(data.get("status", ""))})]


def project(state: ProjectionState, kind: str, data: Any, *, seq: int, time: int = 0) -> tuple[ProjectionState, list[dict]]:
    """投影一帧。kind/data 是宿主帧的 kind 与 data；time 是宿主帧时间，插件帧用它。"""
    if kind == "session/event":
        return _session_event(state, data, seq)
    payload = data if isinstance(data, dict) else {}
    if kind in ("approval/requested", "approval/resolved"):
        return state, _approval(state, kind, payload, seq, time)
    if kind in QUESTION_FRAMES:
        return state, _question(state, kind, payload, seq, time)
    if kind in ("subagent/started", "subagent/finished"):
        return state, _subagent(state, kind, payload, seq, time)
    if kind == "skill-gate/failed":
        turn = _turn(payload, state)
        event = _event("item.failed", seq=seq, turn=turn, time=time, item_id=f"skill-gate-{turn}", kind="error",
                       data={"code": "skill_gate_failed", "required_tool": str(payload.get("requiredTool", "")),
                             "missing": _gate_missing(payload.get("missing"))})
        return replace(state, gate_failed_turn=turn), [event]
    if kind == GAP_KIND:
        event = _event("item.failed", seq=seq, turn=state.turn, time=time, item_id=f"gap-{seq}", kind="error",
                       data={"code": "events_gap", "message": "部分执行事件已丢失"})
        return state, [event]
    return state, []


def _gate_missing(raw) -> list[dict]:
    """技能门缺的步骤：[{skill, tools}]，只保留名字，数量与长度都截断。"""
    items = raw if isinstance(raw, list) else []
    return [{"skill": str(item.get("skill", ""))[:64],
             "tools": [str(tool)[:64] for tool in item.get("tools", [])[:8] if isinstance(tool, str)]}
            for item in items[:16] if isinstance(item, dict) and isinstance(item.get("tools"), list)]
