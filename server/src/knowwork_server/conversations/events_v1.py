"""执行事件模型 events.v1：把宿主帧（DSH 原始会话事件 + KnowWork 插件帧）投影成前端只认的事件。

这里只有纯函数：输入当前投影状态与一帧，
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

from ..tools.presentation import normalize as normalize_presentation

SCHEMA = "events.v1"

RUN_TYPES = ("run.started", "run.completed", "run.failed", "run.cancelled", "run.waiting")
ITEM_TYPES = ("item.started", "item.delta", "item.updated", "item.completed", "item.failed")
ITEM_KINDS = ("message", "thinking", "tool", "approval", "input_request", "todo", "artifact", "deliverable", "evidence",
              "subagent", "background_job", "error")

GAP_KIND = "kernel.gap"
# 不是人发起一轮时那条消息的来源（官方 source.kind）：展示成「谁发起了这一轮」，不当用户的话。
# 定时任务到点；后台任务（官方 dsh-tool-jobs）或在后台跑的子 Agent（dsh-subagent）做完时 Agent 正空闲，官方自己开一轮
# 让它接着处理结果。Agent 正忙时官方把同样的消息塞进当前这一轮，那就不算发起（前端与运行记录只认一轮的第一条）
TRIGGER_TITLES = {"schedule": "定时任务", "tool-jobs": "后台任务完成", "subagent-settled": "子 Agent 完成"}
TRIGGER_SOURCES = tuple(TRIGGER_TITLES)
TEXT_LIMIT = 8000
# 宿主给附件写的说明块（runtime-host attachment-handles.mjs，逐字照官方 dsh-llm 的 fileHandleText）：
# 不进显示文字，换成附件列表。文件名是 JSON 字符串（官方 quoted 即 JSON.stringify）。
_FILE_HANDLE = re.compile(r'^\[File (?P<name>"(?:[^"\\]|\\.)*") \((?P<bytes>\d+) bytes, sha256:[0-9a-f]{8}\): '
                          r'verbatim read-only copy saved at ')
# 解析结果的位置、给模型读的全文或目录（attachment-handles.mjs 的 extractedText、pendingText、contentsText）：同样不进显示文字
_READING_HANDLES = ("[Markdown extracted from ", "[Contents of ")
# 宿主写的 @ 说明块（runtime-host mentions.mjs）：第一行是这个前缀加 JSON，不进显示文字，换成气泡上的小标签
_MENTION_PREFIX = "[用户这一轮 @ 点名的范围] "
MENTION_TYPES = ("knowledge_base", "folder", "document", "tag", "mcp", "skill", "extension")
MAX_MENTIONS = 30
FIELD_LIMIT = 2000
LABEL_LIMIT = 1000
MAX_QUESTIONS = 16
MAX_OPTIONS = 32
MAX_TODOS = 100
MAX_DELIVERABLES = 100
PATH_LIMIT = 4096
MAX_CONDITIONS = 50
MAX_SEARCH_DOCUMENTS = 50
TODO_STATUSES = ("pending", "in_progress", "completed")
MAX_AUTO_APPROVED = 64  # 标了自动批准、还没等到 approval/decided 的；正常几毫秒内就到，这只是兜底
# 向用户提问也只认官方日志（同审批）：ask_user_question 的工具调用、它的结果、迟到回答（官方 user-question-reply 消息），
# 规则同官方 dsh-user-questions 的 userQuestions 会话投影。服务端执行与本机执行的对话一样，不认应答者另发的帧。
ASK_USER = "ask_user_question"
QUESTION_REPLY = "user-question-reply"
OUTCOME_UNKNOWN = "TOOL_OUTCOME_UNKNOWN"  # 宿主崩溃后恢复会话，官方给没结束的调用补的结果：提问转入稍后再答
# 提问没等到回答就关了：官方 UserQuestionError 的码记在工具结果的 error.code 里（2026-11-13 在本机宿主上实测）。
# 超时是 ASK_TIMED_OUT（不限时的调用到了应答者的等待上限、legacy 模式到期），别的（中止、回合取消）都算已失效
ASK_TIMED_OUT = "ASK_TIMED_OUT"
MAX_OPEN_QUESTIONS = 64
# 审批只认官方日志一个来源：官方审批服务（dsh-user-approval）在发起审批的会话日志里记的这一对，
# 卡片编号就是日志里的官方编号，答审批的接口认的也是它。子 Agent 的那一对在它自己的会话里，经 subagent/event 转到根会话。
# 宿主上我们的应答者按「本会话不再询问」自动批准时，另发一个只带官方编号的标记 approval/auto，卡片据此写「自动批准」。
APPROVAL_EVENTS = ("approval/asked", "approval/decided")


@dataclass(frozen=True)
class ProjectionState:
    """跨帧需要记住的最少信息：当前轮次（插件帧不带轮次）、本轮技能门是否已判失败、
    已标成自动批准、还没答完的审批（approval/auto 先到，日志里的 approval/decided 后到）、提问的模式与还能答的提问。"""

    turn: int | None = None
    gate_failed_turn: int | None = None
    auto_approved: frozenset[str] = frozenset()
    timed_questions: bool = False  # 最近一次请求头里 ask_user_question 是不是计时模式（能转入稍后再答）
    questions: frozenset[str] = frozenset()  # 还能答的提问（开着或转入稍后再答）的工具调用 id


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


def _joined(content: Any, block_types: tuple[str, ...]) -> str:
    if not isinstance(content, list):
        return ""
    return "".join(b.get("text", "") for b in content
                   if isinstance(b, dict) and b.get("type") in block_types and isinstance(b.get("text"), str))


def assistant_text(payload: Any) -> str:
    """助手消息帧的正文，不截断（核对引用编号要看全文）：content 可能是字符串，也可能是块列表，只取文本块。"""
    message = payload.get("message") if isinstance(payload, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    return content if isinstance(content, str) else _joined(content, ("text",))


def _texts(content: Any, block_types: tuple[str, ...]) -> str:
    return _joined(content, block_types)[:TEXT_LIMIT]


def _labels(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(x)[:LABEL_LIMIT] for x in value if isinstance(x, str)][:MAX_CONDITIONS]


def _search_summary(text: str) -> dict | None:
    """知识检索结果（knowledge/tools.py 的 knowledge_search）的摘要：命中哪些文档、用了与撤掉了哪些条件。

    界面拿它在回答上方列出检索条件、让用户撤掉重答；结果原文会被截到 TEXT_LIMIT，界面自己解析不了，
    所以在这里按完整原文摘。工具结果帧不带工具名，按结果的形状认：documents 里每份都有 doc_ref、source_id
    和 results 列表，顶层带 notes（knowledge_documents 的结果顶层是 knowledge_base，不算）。2026-10-25 之前
    的结果不分组（results 里每条都有 doc_ref 与 source_id），回放旧会话时照样认。
    """
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    found = _grouped_hits(parsed) if isinstance(parsed, dict) else None
    if found is None:
        return None
    documents = list(dict.fromkeys(f"{r['source_id']}:{r['doc_ref']}" for r in found))
    return {"documents": documents[:MAX_SEARCH_DOCUMENTS], "conditions": _labels(parsed.get("conditions")),
            "removed": _labels(parsed.get("conditions_removed"))}


def _cited(items: Any) -> bool:
    return isinstance(items, list) and all(
        isinstance(r, dict) and isinstance(r.get("doc_ref"), str) and "source_id" in r for r in items)


def _grouped_hits(parsed: dict) -> list[dict] | None:
    """检索结果里的文档（每份一条），认不出是检索结果时为 None。"""
    documents = parsed.get("documents")
    if (_cited(documents) and isinstance(parsed.get("notes"), list) and "knowledge_base" not in parsed
            and all(isinstance(d.get("results"), list) for d in documents)):
        return documents
    results = parsed.get("results")
    return results if _cited(results) else None


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
    子 Agent 回报等，它们的 source.kind 不是 user，不展示给用户；定时任务到点投进来的展示成「谁发起了这一轮」。"""
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    if source.get("kind") in TRIGGER_SOURCES:
        return _trigger(state, payload, source, seq, time)
    if source.get("kind") != "user":
        return []
    text, attachments, mentions = user_parts(payload.get("content"))
    if not text and not attachments:
        return []
    message_id = str(payload.get("id") or f"user-{seq}")
    data = {"role": "user", "text": text, **({"attachments": attachments} if attachments else {}),
            **({"mentions": mentions} if mentions else {})}
    return [_event("item.completed", seq=seq, turn=state.turn, time=time, item_id=message_id, kind="message",
                   data=data)]


def _trigger(state: ProjectionState, payload: dict, source: dict, seq: int, time: int) -> list[dict]:
    """不是人发起的一轮（照官方对话界面：一条可展开的通知，写明是什么触发的）。"""
    text = trigger_text(source, payload.get("content"))[:TEXT_LIMIT]
    message_id = str(payload.get("id") or f"trigger-{seq}")
    return [_event("item.completed", seq=seq, turn=state.turn, time=time, item_id=message_id, kind="message",
                   data={"role": "trigger", "source": str(source["kind"]), "text": text})]


def trigger_text(source: dict, content: Any) -> str:
    """自动发起的那条消息里给人看的部分：定时任务是各任务的提示词（空行隔开）；后台任务、子 Agent 是官方写在来源上的
    一句摘要（命令与退出状态、哪个子 Agent 怎么结束的），没有摘要时给全文。"""
    text = _joined(content, ("text",))
    if source.get("kind") == "schedule":
        return "\n\n".join(reminder_prompts(text))
    summary = source.get("summary")
    return summary if isinstance(summary, str) and summary else text


def reminder_prompts(text: str) -> list[str]:
    """官方定时任务的提醒正文（dsh-schedule 的 renderReminderFraming / renderRecurringReminderBatchFraming）里
    各任务的提示词：单条是 reminder_prompt_json 一行，合并投递是 reminders_json 一行。认不出来就原样给全文。"""
    for line in text.splitlines():
        key, _, value = line.partition(": ")
        try:
            if key == "reminder_prompt_json":
                prompt = json.loads(value)
                return [prompt] if isinstance(prompt, str) else [text]
            if key == "reminders_json":
                items = json.loads(value)
                prompts = [i["reminder_prompt"] for i in items if isinstance(i, dict)
                           and isinstance(i.get("reminder_prompt"), str)] if isinstance(items, list) else []
                return prompts or [text]
        except ValueError:
            return [text]
    return [text]


def _mentions(block: str) -> list[dict] | None:
    """@ 说明块的第一行；不是说明块返回 None。留类型、id、名字、所在知识库，扩展的另留类型标识与类型名
    （重新生成时前端照原样再发）。"""
    if not block.startswith(_MENTION_PREFIX):
        return None
    try:
        items = json.loads(block[len(_MENTION_PREFIX):].split("\n", 1)[0])
    except ValueError:
        return None
    if not isinstance(items, list):
        return None
    def field(item: dict, key: str) -> dict:
        return {key: str(item[key])[:LABEL_LIMIT]} if isinstance(item.get(key), str) else {}

    return [{"type": item["type"], "id": str(item["id"])[:LABEL_LIMIT], "name": str(item["name"])[:LABEL_LIMIT],
             **field(item, "base"), **({"base_name": item["baseName"][:LABEL_LIMIT]}
                                       if isinstance(item.get("baseName"), str) else {}),
             **field(item, "kind"), **field(item, "label")}
            for item in items[:MAX_MENTIONS]
            if isinstance(item, dict) and item.get("type") in MENTION_TYPES and isinstance(item.get("name"), str)
            and isinstance(item.get("id"), str)]


def user_parts(content: Any) -> tuple[str, list[dict], list[dict]]:
    """用户原话、附件与 @：附件、@ 的说明块按固定格式认出来。附件只留文件名与大小（路径是沙箱内部的，不给浏览器）。"""
    texts: list[str] = []
    attachments: list[dict] = []
    mentions: list[dict] = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict) or block.get("type") != "text" or not isinstance(block.get("text"), str):
            continue
        mentioned = _mentions(block["text"])
        if mentioned is not None:
            mentions.extend(mentioned)
            continue
        found = _FILE_HANDLE.match(block["text"])
        if found is not None:
            try:
                attachments.append({"name": str(json.loads(found["name"])), "size": int(found["bytes"])})
            except ValueError:
                texts.append(block["text"])
        elif not block["text"].startswith(_READING_HANDLES):
            texts.append(block["text"])
    return "".join(texts)[:TEXT_LIMIT], attachments, mentions


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
    full = _joined(message.get("content"), ("text",))
    data: dict = {"text": full[:TEXT_LIMIT]}
    search = None if failed else _search_summary(full)
    if search is not None:
        data["search"] = search
    # 工具交的界面卡片（DSH tool/result 的 meta，业务工具经工具桥的 presentationMeta 交来）；交给浏览器前再校验截断一次
    presentation = None if failed else normalize_presentation(payload.get("meta"))
    if presentation is not None:
        data["presentation"] = presentation
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
    if kind == "request/header":
        return _question_mode(state, payload), []
    if kind == "user/message":
        answered, events = _question_reply(state, payload, seq, time)
        return answered, events + _user_message(state, payload, seq, time)
    if kind == "tool/call":
        asked, events = _question_asked(state, payload, seq, time)
        return asked, _tool_call(state, payload, seq, time) + events
    if kind == "tool/result":
        settled, events = _question_result(state, payload, seq, time)
        return settled, _tool_result(state, payload, seq, time) + events
    if kind == "todo/write":
        return state, _todos(state, payload, seq, time)
    if kind == "tool/ptc-dispatch":
        return _question_dispatched(state, payload, seq, time)
    if kind == "deliverables/presented":
        return state, _deliverables(state, payload, seq, time)
    if kind in APPROVAL_EVENTS:
        return _approval(state, kind, payload, seq, time)
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


def _deliverables(state: ProjectionState, payload: dict, seq: int, time: int) -> list[dict]:
    """官方 present 交付的文件（deliverables/presented）：路径照宿主给的原样（服务端会话是 /sessions/<会话>/...，本机会话是
    用户电脑上的路径），带说明的带上。网页与桌面都按它出交付卡片、按路径读（方案 7.7.3）；一次交付一个条目，
    同一路径交付多次由前端用最近一次的说明（同官方交付卡片）。"""
    raw = payload.get("files")
    if not isinstance(raw, list):
        return []
    files = [{"path": item["path"][:PATH_LIMIT],
              **({"description": item["description"][:FIELD_LIMIT]} if isinstance(item.get("description"), str) else {})}
             for item in raw[:MAX_DELIVERABLES]
             if isinstance(item, dict) and isinstance(item.get("path"), str) and item["path"]]
    if not files:
        return []
    return [_event("item.completed", seq=seq, turn=state.turn, time=time, item_id=f"deliverable-{seq}",
                   kind="deliverable", data={"files": files})]


def _approval(state: ProjectionState, kind: str, data: dict, seq: int, time: int) -> tuple[ProjectionState, list[dict]]:
    """日志里的一对：approval/asked → 卡片出现、这一轮等审批；approval/decided → 卡片结束（自动批准的带 auto）。"""
    approval_id = data.get("id")
    if not isinstance(approval_id, str):
        return state, []
    if kind == "approval/decided":
        auto = approval_id in state.auto_approved
        done = _event("item.completed", seq=seq, turn=state.turn, time=time, item_id=approval_id, kind="approval",
                      data={"outcome": str(data.get("outcome", "")), **({"auto": True} if auto else {})})
        return replace(state, auto_approved=state.auto_approved - {approval_id}), [done]
    detail = {"tool_name": str(data.get("toolName", "")), "call_id": data.get("callId"), "reason": data.get("reason")}
    return state, [
        _event("item.started", seq=seq, turn=state.turn, time=time, item_id=approval_id, kind="approval", data=detail),
        _event("run.waiting", seq=seq, turn=state.turn, time=time, item_id=approval_id,
               data={"reason": "approval"}),
    ]


def _subagent_event(state: ProjectionState, data: dict, seq: int, time: int) -> tuple[ProjectionState, list[dict]]:
    """子 Agent 自己会话里的事件（宿主或设备转发来的 {sessionId, event}）：现在只转审批，卡片出在根会话上。"""
    event = data.get("event") if isinstance(data.get("event"), dict) else {}
    kind = event.get("type")
    if kind not in APPROVAL_EVENTS:
        return state, []
    payload = event.get("data") if isinstance(event.get("data"), dict) else {}
    return _approval(state, kind, payload, seq, _int(event.get("time")) or time)


def _auto_approved(state: ProjectionState, data: dict) -> tuple[ProjectionState, list[dict]]:
    approval_id = data.get("id")
    if not isinstance(approval_id, str) or len(state.auto_approved) >= MAX_AUTO_APPROVED:
        return state, []
    return replace(state, auto_approved=state.auto_approved | {approval_id}), []


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


def _question_mode(state: ProjectionState, payload: dict) -> ProjectionState:
    """请求头里的工具定义：ask_user_question 带 timeout 参数就是计时模式（同官方 isTimedAskUserQuestionSchema）。"""
    header = payload.get("header") if isinstance(payload.get("header"), dict) else {}
    tools = header.get("tools") if isinstance(header.get("tools"), list) else []
    timed = any(isinstance(tool, dict) and tool.get("name") == ASK_USER and isinstance(tool.get("parameters"), dict)
                and isinstance(tool["parameters"].get("properties"), dict) and "timeout" in tool["parameters"]["properties"]
                for tool in tools)
    return state if timed == state.timed_questions else replace(state, timed_questions=timed)


def _json_text(content: Any) -> tuple[str, Any]:
    """消息内容里第一段文字，和它按 JSON 读出来的样子（读不出来是 None）。"""
    blocks = content if isinstance(content, list) else []
    text = next((block.get("text") for block in blocks
                 if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)), "")
    try:
        return text, json.loads(text)
    except ValueError:
        return text, None


def _question_common(state: ProjectionState, call_id: str, seq: int, time: int) -> dict:
    return {"seq": seq, "turn": state.turn, "time": time, "item_id": call_id, "kind": "input_request"}


def _question_asked(state: ProjectionState, payload: dict, seq: int, time: int) -> tuple[ProjectionState, list[dict]]:
    """模型调 ask_user_question：卡片出现、这一轮等用户。item_id 是工具调用 id，回答与迟到回答都按它找回这张卡。"""
    call_id = payload.get("callId")
    if payload.get("name") != ASK_USER or not isinstance(call_id, str):
        return state, []
    raw = payload.get("arguments")
    try:
        arguments = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return state, []
    questions = _questions(arguments.get("questions")) if isinstance(arguments, dict) else []
    if not questions or len(state.questions) >= MAX_OPEN_QUESTIONS:
        return state, []
    common = _question_common(state, call_id, seq, time)
    return replace(state, questions=state.questions | {call_id}), [
        _event("item.started", **common, data={"questions": questions, "timed": state.timed_questions}),
        _event("run.waiting", seq=seq, turn=state.turn, time=time, item_id=call_id, data={"reason": "input"}),
    ]


def _question_result(state: ProjectionState, payload: dict, seq: int, time: int) -> tuple[ProjectionState, list[dict]]:
    """提问的工具结果（同官方 userQuestions 投影）：带 answers 是答了；pending、或宿主崩溃后官方补的「结果未知」是转入
    稍后再答（问题仍可答）；别的失败是关闭（超时，或中止——回合取消、宿主正常关闭）。"""
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    call_id = message.get("toolCallId")
    if call_id not in state.questions:
        return state, []
    common = _question_common(state, call_id, seq, time)
    _, parsed = _json_text(message.get("content"))
    error = payload.get("error") if isinstance(payload.get("error"), dict) else None
    if (isinstance(parsed, dict) and parsed.get("pending") is True) or (error or {}).get("code") == OUTCOME_UNKNOWN:
        return state, [_event("item.updated", **common, data={"state": "continued"})]
    rest = replace(state, questions=state.questions - {call_id})
    if error is None and message.get("isError") is not True and isinstance(parsed, dict) \
            and isinstance(parsed.get("answers"), list):
        return rest, [_event("item.completed", **common, data={"answers": _answers(parsed["answers"]), "late": False})]
    reason = "timeout" if (error or {}).get("code") == ASK_TIMED_OUT else "cancelled"
    return rest, [_event("item.failed", **common, data={"reason": reason})]


def _question_dispatched(state: ProjectionState, payload: dict, seq: int,
                         time: int) -> tuple[ProjectionState, list[dict]]:
    """PTC 里发起的提问（官方 tool/ptc-dispatch）：同官方 userQuestions 投影，只认已转入稍后再答的那种（PTC 不在前台等人，
    提问一发出就是 pending），出一张能稍后再答的卡片；item_id 是 subCallId，迟到回答按它找回这张卡。"""
    call_id = payload.get("subCallId")
    if payload.get("name") != ASK_USER or payload.get("isError") is True or not isinstance(call_id, str):
        return state, []
    _, parsed = _json_text(payload.get("content"))
    arguments = payload.get("arguments")
    questions = _questions(arguments.get("questions")) if isinstance(arguments, dict) else []
    if not (isinstance(parsed, dict) and parsed.get("pending") is True) or not questions \
            or len(state.questions) >= MAX_OPEN_QUESTIONS:
        return state, []
    common = _question_common(state, call_id, seq, time)
    return replace(state, questions=state.questions | {call_id}), [
        _event("item.started", **common, data={"questions": questions, "timed": True}),
        _event("item.updated", **common, data={"state": "continued"}),
    ]


def _question_reply(state: ProjectionState, payload: dict, seq: int, time: int) -> tuple[ProjectionState, list[dict]]:
    """转入稍后再答的提问收到回答：官方把它作为 user-question-reply 用户消息送进来，进日志时算答了（迟到回答，
    落在后来那一轮里，前端按 item_id 找回原卡片）。"""
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    call_id = source.get("callId")
    if source.get("kind") != QUESTION_REPLY or call_id not in state.questions:
        return state, []
    _, parsed = _json_text(payload.get("content"))
    answers = _answers(parsed.get("answers")) if isinstance(parsed, dict) else []
    return replace(state, questions=state.questions - {call_id}), [
        _event("item.completed", **_question_common(state, call_id, seq, time), data={"answers": answers, "late": True})]


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
    if kind == "approval/auto":
        return _auto_approved(state, payload)
    if kind == "subagent/event":
        return _subagent_event(state, payload, seq, time)
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
