"""events.v1 映射：DSH 原始会话事件与插件帧 → 前端事件。纯函数，逐类覆盖。"""

import json

import pytest

from knowwork_server.conversations.events_v1 import GAP_KIND, ITEM_KINDS, RUN_TYPES, ProjectionState, project

S0 = ProjectionState()


def session(type_, data=None, time=100):
    return {"type": type_, "seq": 1, "time": time, "data": data or {}}


def one(state, kind, data, seq=7, time=5):
    new_state, events = project(state, kind, data, seq=seq, time=time)
    return new_state, events


def test_every_event_has_the_envelope():
    _, events = one(S0, "session/event", session("turn/start", {"turn": 3}))
    assert events == [{"schema_version": "events.v1", "type": "run.started", "seq": 7, "run_id": "run-3", "time": 100,
                       "item_id": None, "kind": None, "data": {}}]


def test_turn_start_remembers_turn_for_plugin_frames():
    state, _ = one(S0, "session/event", session("turn/start", {"turn": 2}))
    _, events = one(state, "subagent/started", {"parentSessionId": "p", "childSessionId": "child"})
    assert events[0]["run_id"] == "run-2", "插件帧不带轮次，用最近一次 turn/start 的"


def test_approvals_come_from_the_official_log_with_the_official_id():
    """审批只认官方日志一个来源：官方审批服务记的 approval/asked、approval/decided，卡片编号就是官方编号；
    服务端执行和本机执行的对话一样，不再有应答者另发的审批帧。"""
    state, _ = one(S0, "session/event", session("turn/start", {"turn": 2}))
    asked = session("approval/asked", {"id": "ap1", "toolName": "kw_write", "callId": "c1", "reason": "写入"})
    _, started = one(state, "session/event", asked)
    assert [(e["type"], e["kind"], e["item_id"], e["run_id"]) for e in started] == [
        ("item.started", "approval", "ap1", "run-2"), ("run.waiting", None, "ap1", "run-2")]
    assert started[0]["data"] == {"tool_name": "kw_write", "call_id": "c1", "reason": "写入"}
    assert started[1]["data"] == {"reason": "approval"}
    _, done = one(state, "session/event", session("approval/decided", {"id": "ap1", "outcome": "rejected"}))
    assert [(e["type"], e["kind"], e["data"]) for e in done] == [("item.completed", "approval", {"outcome": "rejected"})]
    for old in ("approval/requested", "approval/resolved", "approval/granted"):
        assert one(state, old, {"id": "x", "toolName": "kw_write", "outcome": "rejected"})[1] == [], f"{old} 不再认"


def test_auto_approval_marker_labels_the_decided_card():
    """「本会话不再询问」自动批准：日志里照样一问一答，应答者在中间另发只带官方编号的 approval/auto，卡片据此标 auto。"""
    state, _ = one(S0, "session/event", session("approval/asked", {"id": "ap1", "toolName": "kw_write"}))
    state, marked = one(state, "approval/auto", {"id": "ap1", "sessionId": "s"})
    assert marked == [] and "ap1" in state.auto_approved
    state, done = one(state, "session/event", session("approval/decided", {"id": "ap1", "outcome": "allowed-once"}))
    assert done[0]["data"] == {"outcome": "allowed-once", "auto": True}
    assert state.auto_approved == frozenset(), "答完就忘掉"
    _, other = one(state, "session/event", session("approval/decided", {"id": "ap2", "outcome": "allowed-once"}))
    assert other[0]["data"] == {"outcome": "allowed-once"}, "人批的不标"


def test_subagent_approvals_show_up_on_the_root_conversation():
    """子 Agent 的审批记在它自己的会话日志里，宿主（或设备）转成 subagent/event 送来：卡片出在根会话上，编号同样是官方的。"""
    asked = {"sessionId": "child", "event": session("approval/asked", {"id": "ap9", "toolName": "bash", "callId": "c9"}, time=42)}
    _, started = one(S0, "subagent/event", asked)
    assert [(e["type"], e["item_id"], e["time"]) for e in started] == [("item.started", "ap9", 42), ("run.waiting", "ap9", 42)]
    decided = {"sessionId": "child", "event": session("approval/decided", {"id": "ap9", "outcome": "allowed-once"})}
    assert one(S0, "subagent/event", decided)[1][0]["data"] == {"outcome": "allowed-once"}
    other = {"sessionId": "child", "event": session("tool/call", {"callId": "c9"})}
    assert one(S0, "subagent/event", other)[1] == [], "子 Agent 的其他事件照旧不转"


@pytest.mark.parametrize(("reason", "expected", "data"), [
    ({"kind": "completed"}, "run.completed", {}),
    ({"kind": "max-tokens"}, "run.completed", {"reason": "max_tokens"}),
    ({"kind": "aborted", "reason": {"kind": "user"}}, "run.cancelled", {}),
    ({"kind": "error", "error": {"code": "RATE_LIMIT", "message": "secret upstream detail"}}, "run.failed",
     {"code": "RATE_LIMIT", "message": "本轮执行失败"}),
    ({"kind": "blocked"}, "run.failed", {"code": "blocked", "message": "本轮被拦截"}),
])
def test_turn_end_reasons(reason, expected, data):
    _, events = one(S0, "session/event", session("turn/end", {"turn": 1, "reason": reason}))
    assert [(e["type"], e["data"]) for e in events] == [(expected, data)]
    assert "secret" not in str(events), "上游错误原文不应推给前端"


def test_skill_gate_failure_turns_completed_into_failed():
    state, _ = one(S0, "session/event", session("turn/start", {"turn": 4}))
    state, gate = one(state, "skill-gate/failed", {"turn": 4, "requiredTool": "kw_check", "nudges": 2})
    assert gate[0]["type"] == "item.failed" and gate[0]["kind"] == "error"
    assert gate[0]["data"]["code"] == "skill_gate_failed"
    state, end = one(state, "session/event", session("turn/end", {"turn": 4, "reason": {"kind": "completed"}}))
    assert end[0]["type"] == "run.failed" and end[0]["data"]["code"] == "skill_gate_failed"
    _, next_turn = one(state, "session/event", session("turn/end", {"turn": 5, "reason": {"kind": "completed"}}))
    assert next_turn[0]["type"] == "run.completed", "技能门失败只影响当轮"


def test_assistant_message_yields_thinking_and_message_items():
    message = {"id": "m1", "role": "assistant", "content": [
        {"type": "reasoning", "text": "想一想"}, {"type": "text", "text": "你好"}, {"type": "text", "text": "！"},
        {"type": "tool-call", "id": "c1", "name": "x", "arguments": "{}"},
    ]}
    _, events = one(S0, "session/event", session("assistant/message", {"turn": 1, "step": 1, "message": message}))
    assert [(e["type"], e["kind"], e["item_id"], e["data"]) for e in events] == [
        ("item.completed", "thinking", "m1:thinking", {"text": "想一想"}),
        ("item.completed", "message", "m1", {"role": "assistant", "text": "你好！"}),
    ]


def test_tool_call_and_result():
    _, started = one(S0, "session/event",
                     session("tool/call", {"turn": 1, "step": 1, "callId": "c1", "name": "kw_read", "arguments": {"a": 1}}))
    assert started[0]["type"] == "item.started" and started[0]["kind"] == "tool" and started[0]["item_id"] == "c1"
    assert started[0]["data"] == {"name": "kw_read", "arguments": {"a": 1}}
    ok = {"turn": 1, "message": {"toolCallId": "c1", "isError": False, "content": [{"type": "text", "text": "结果"}]}}
    _, done = one(S0, "session/event", session("tool/result", ok))
    assert (done[0]["type"], done[0]["item_id"], done[0]["data"]) == ("item.completed", "c1", {"text": "结果"})
    bad = {"turn": 1, "error": {"code": "TOOL_FAILED"}, "message": {"toolCallId": "c1", "isError": True, "content": []}}
    _, failed = one(S0, "session/event", session("tool/result", bad))
    assert failed[0]["type"] == "item.failed" and failed[0]["data"]["code"] == "TOOL_FAILED"


def test_tool_results_carry_the_card_the_tool_left_for_the_ui():
    """DSH tool/result 的 meta（业务工具经工具桥的 presentationMeta 交来）投影成 presentation，交给浏览器前再校验一次。"""
    card = {"version": 1, "cards": [{"type": "table", "columns": [{"name": "n"}], "rows": [[1]], "row_count": 1}]}
    ok = {"turn": 1, "message": {"toolCallId": "c1", "isError": False, "content": [{"type": "text", "text": "结果"}]},
          "meta": card}
    _, [event] = one(S0, "session/event", session("tool/result", ok))
    assert event["data"]["presentation"]["cards"][0]["rows"] == [[1]]
    for meta in ({"diff": "dsh-tool-fs 的上下文"}, {"version": 1, "cards": [{"type": "script", "src": "x"}]}):
        _, [event] = one(S0, "session/event", session("tool/result", {**ok, "meta": meta}))
        assert "presentation" not in event["data"], "别的工具的 meta、认不出的卡片不交给浏览器"
    failed = {**ok, "message": {**ok["message"], "isError": True}}
    _, [event] = one(S0, "session/event", session("tool/result", failed))
    assert "presentation" not in event["data"]


def test_knowledge_search_results_carry_a_summary_for_the_conditions_bar():
    """检索结果带摘要：命中哪些文档（去重）、用了与撤掉了哪些条件。按完整原文摘，结果再长、显示文字被截断也不影响。"""
    hits = [{"ref": i, "source_id": "builtin", "doc_ref": f"kb1/d{i % 3}", "text": "长" * 3000} for i in range(6)]
    text = json.dumps({"results": hits, "notes": [], "conditions": ["年度 = 2025"], "conditions_removed": ["部门 = 财务部"]},
                      ensure_ascii=False)
    found = {"turn": 1, "message": {"toolCallId": "c1", "content": [{"type": "text", "text": text}]}}
    _, [event] = one(S0, "session/event", session("tool/result", found))
    assert len(event["data"]["text"]) == 8000
    assert event["data"]["search"] == {"documents": ["builtin:kb1/d0", "builtin:kb1/d1", "builtin:kb1/d2"],
                                       "conditions": ["年度 = 2025"], "removed": ["部门 = 财务部"]}
    for other in ("结果", json.dumps({"results": [{"title": "别的工具"}]}), json.dumps([1, 2])):
        plain = {"turn": 1, "message": {"toolCallId": "c2", "content": [{"type": "text", "text": other}]}}
        _, [event] = one(S0, "session/event", session("tool/result", plain))
        assert "search" not in event["data"], other


def test_grouped_search_results_are_summarised_and_listed_documents_are_not():
    """检索结果按文档分组后照样摘；knowledge_documents 的结果也有 documents，但顶层是 knowledge_base，不算检索。"""
    docs = [{"source_id": "builtin", "doc_ref": f"kb1/d{i}", "title": "t", "results": [{"ref": i, "text": "x"}]}
            for i in range(2)]
    grouped = json.dumps({"documents": docs, "notes": [], "conditions": ["年度 = 2025"]}, ensure_ascii=False)
    listed = json.dumps({"knowledge_base": "builtin.kb1", "source_id": "builtin",
                         "documents": [{"doc_ref": "kb1/d0", "title": "t"}], "notes": []})
    empty = json.dumps({"documents": [], "notes": ["在可读的知识范围内没有找到相关内容"]}, ensure_ascii=False)
    summaries = []
    for text in (grouped, listed, empty):
        found = {"turn": 1, "message": {"toolCallId": "c3", "content": [{"type": "text", "text": text}]}}
        _, [event] = one(S0, "session/event", session("tool/result", found))
        summaries.append(event["data"].get("search"))
    assert summaries == [{"documents": ["builtin:kb1/d0", "builtin:kb1/d1"], "conditions": ["年度 = 2025"], "removed": []},
                         None, {"documents": [], "conditions": [], "removed": []}]


def test_subagent_started_and_finished():
    _, started = one(S0, "subagent/started", {"parentSessionId": "p", "childSessionId": "child"})
    _, finished = one(S0, "subagent/finished", {"childSessionId": "child", "status": "completed"})
    assert (started[0]["type"], started[0]["kind"], started[0]["item_id"]) == ("item.started", "subagent", "child")
    assert finished[0]["data"] == {"status": "completed"}


def test_gap_is_reported_as_error_item():
    _, events = one(S0, GAP_KIND, {"after": 3, "oldest": 10}, seq=9)
    assert events[0]["type"] == "item.failed" and events[0]["kind"] == "error"
    assert events[0]["data"]["code"] == "events_gap" and events[0]["item_id"] == "gap-9"


@pytest.mark.parametrize(("kind", "data"), [
    ("session/event", session("step/start", {"turn": 1})),
    ("session/event", session("user/message", {"id": "u"})),
    ("session/event", session("brand-new/type")),
    ("session/event", "not-a-dict"),
    ("agent/status", {"status": "running"}),
    ("skill-gate/passed", {"turn": 1}),
    ("kernel.invalid", {"reason": "invalid_frame"}),
    ("something/unknown", None),
    ("session/event", session("approval/asked", {"no": "id"})),
    ("subagent/event", {"sessionId": "child", "event": "not-a-dict"}),
    ("session/event", session("tool/call", {"callId": 5})),
])
def test_unknown_or_malformed_frames_are_not_pushed(kind, data):
    state, events = one(S0, kind, data)
    assert events == [] and state == S0


def test_projection_is_pure():
    state = ProjectionState(turn=1)
    data = session("turn/end", {"turn": 1, "reason": {"kind": "completed"}})
    snapshot = repr(data)
    first = project(state, "session/event", data, seq=1)
    second = project(state, "session/event", data, seq=1)
    assert first == second and repr(data) == snapshot and state == ProjectionState(turn=1)


def test_vocabulary_matches_design():
    assert set(RUN_TYPES) == {"run.started", "run.completed", "run.failed", "run.cancelled", "run.waiting"}
    assert {"message", "tool", "approval", "input_request", "subagent", "error"} <= set(ITEM_KINDS)


def test_user_messages_are_projected_into_the_current_run_and_injected_ones_are_not():
    """用户亲自发的消息（source=user）投影成 role=user 的消息，归到当前这一轮；运行时注入的上下文等不展示。"""
    state, _ = project(ProjectionState(), "session/event", session("turn/start", {"turn": 3}), seq=1)
    mine = {"id": "u-1", "role": "user", "source": {"kind": "user"}, "content": [{"type": "text", "text": "宿舍退宿有什么规定？"}]}
    _, events = project(state, "session/event", session("user/message", mine), seq=2)
    [event] = events
    assert (event["type"], event["kind"], event["item_id"], event["run_id"]) == ("item.completed", "message", "u-1", "run-3")
    assert event["data"] == {"role": "user", "text": "宿舍退宿有什么规定？"}
    # 子 Agent、后台任务做完的通知另投影成 trigger（见下面两条），这里只看纯注入的上下文
    for source in ("runtime-context", "agent-message", "skill-catalog"):
        injected = {**mine, "id": "x", "source": {"kind": source}}
        assert project(state, "session/event", session("user/message", injected), seq=3)[1] == []
    empty = {**mine, "content": [{"type": "image", "url": "x"}]}
    assert project(state, "session/event", session("user/message", empty), seq=4)[1] == []


def test_schedule_reminders_become_a_trigger_with_the_task_prompts():
    """定时任务到点投进来的消息（source=schedule）不当用户的话，投影成 role=trigger 的消息，文字是任务的提示词
    （照官方 dsh-schedule 的单条与合并两种提醒正文取）；认不出来就给全文。"""
    state, _ = project(ProjectionState(), "session/event", session("turn/start", {"turn": 2}), seq=1)

    def reminder(text, id_="s-1"):
        message = {"id": id_, "role": "user", "source": {"kind": "schedule"}, "content": [{"type": "text", "text": text}]}
        [event] = project(state, "session/event", session("user/message", message), seq=2)[1]
        return event

    single = ('[SCHEDULE REMINDER]\n说明\nschedule_id_json: "t1"\noccurrence_at: 2026-11-06T01:00:00Z\n'
              'reminder_prompt_json: "写日报：\\n列出今天的进展"')
    event = reminder(single)
    assert (event["kind"], event["item_id"], event["run_id"]) == ("message", "s-1", "run-2")
    assert event["data"] == {"role": "trigger", "source": "schedule", "text": "写日报：\n列出今天的进展"}
    batch = "\n".join(["[SCHEDULE REMINDER BATCH]", "说明", "reminders_json: " + json.dumps([
        {"schedule_id": "t1", "occurrence_at": "x", "reminder_prompt": "写日报"},
        {"schedule_id": "t2", "occurrence_at": "x", "reminder_prompt": "看看库存"}])])
    assert reminder(batch)["data"]["text"] == "写日报\n\n看看库存"
    assert reminder("官方改了正文格式")["data"]["text"] == "官方改了正文格式"
    assert reminder("reminder_prompt_json: {坏的")["data"]["text"] == "reminder_prompt_json: {坏的"


def test_finished_background_work_that_opens_a_turn_is_a_trigger_with_the_official_summary():
    """后台任务、后台子 Agent 做完时 Agent 正空闲，官方自己开一轮：同样投影成 role=trigger，文字取官方写在来源上的摘要，
    没有摘要给全文。"""
    state, _ = project(ProjectionState(), "session/event", session("turn/start", {"turn": 4}), seq=1)

    def notice(kind, text, summary=None):
        source = {"kind": kind, "form": "notice", **({"summary": summary} if summary is not None else {})}
        message = {"id": "n-1", "role": "user", "source": source, "content": [{"type": "text", "text": text}]}
        [event] = project(state, "session/event", session("user/message", message), seq=2)[1]
        return event["data"]

    assert notice("tool-jobs", "长通知……", "bash npm test [status: exited, code 0]") == {
        "role": "trigger", "source": "tool-jobs", "text": "bash npm test [status: exited, code 0]"}
    assert notice("subagent-settled", "全文", "Subagent s2 finished.")["text"] == "Subagent s2 finished."
    assert notice("tool-jobs", "只有全文")["text"] == "只有全文"


TIMED_HEADER = session("request/header", {"header": {"tools": [
    {"name": "ask_user_question", "parameters": {"properties": {"questions": {}, "timeout": {}}}}]}})
QUESTIONS = [{"id": "fmt", "question": "导出成什么格式？", "header": "格式", "multiSelect": False,
              "options": [{"label": "Excel (Recommended)", "description": "表格"}, {"label": "PDF"}]}]


def ask(call_id="call_1", questions=QUESTIONS):
    return session("tool/call", {"callId": call_id, "name": "ask_user_question",
                                 "arguments": json.dumps({"questions": questions, "timeout": 60})})


def ask_result(call_id, body, *, is_error=False, error=None):
    text = body if isinstance(body, str) else json.dumps(body)
    return session("tool/result", {"message": {"toolCallId": call_id, "isError": is_error,
                                               "content": [{"type": "text", "text": text}]},
                                   **({"error": error} if error else {})})


def test_questions_come_from_the_official_log():
    """向用户提问只认官方日志（同官方 userQuestions 投影）：ask_user_question 的调用出卡片，结果里有回答就是答了，
    item_id 是工具调用 id；服务端执行与本机执行的对话一样，不认应答者另发的帧。"""
    state, _ = one(S0, "session/event", session("turn/start", {"turn": 2}))
    state, _ = one(state, "session/event", TIMED_HEADER)
    asked_state, asked = one(state, "session/event", ask())
    questions = [e for e in asked if e["kind"] == "input_request" or e["type"] == "run.waiting"]
    assert [(e["type"], e["kind"], e["item_id"], e["run_id"]) for e in questions] == [
        ("item.started", "input_request", "call_1", "run-2"), ("run.waiting", None, "call_1", "run-2")]
    assert questions[0]["data"] == {"timed": True, "questions": [{
        "id": "fmt", "question": "导出成什么格式？", "header": "格式", "detail": None, "multi_select": False,
        "options": [{"label": "Excel (Recommended)", "description": "表格"}, {"label": "PDF"}]}]}
    assert any(e["kind"] == "tool" for e in asked), "工具调用本身照旧是一个步骤"
    done_state, answered = one(asked_state, "session/event", ask_result("call_1", {"answers": [{"id": "fmt", "selected": ["PDF"]}]}))
    [card] = [e for e in answered if e["kind"] == "input_request"]
    assert (card["type"], card["data"]) == ("item.completed", {"answers": [{"id": "fmt", "selected": ["PDF"]}], "late": False})
    assert done_state.questions == frozenset()
    for old in ("question/requested", "question/answered", "question/continued", "question/closed"):
        assert one(state, old, {"id": "call_1", "questions": QUESTIONS})[1] == [], f"{old} 不再认"


def test_pending_questions_continue_and_late_replies_settle_them():
    state, _ = one(S0, "session/event", TIMED_HEADER)
    state, _ = one(state, "session/event", ask())
    state, pending = one(state, "session/event", ask_result("call_1", {"pending": True, "callId": "call_1", "message": "…"}))
    assert [(e["type"], e["data"]) for e in pending if e["kind"] == "input_request"] == [("item.updated", {"state": "continued"})]
    assert "call_1" in state.questions, "转入稍后再答的仍能答"
    reply = session("user/message", {"source": {"kind": "user-question-reply", "callId": "call_1", "outcome": "answered"},
                                     "content": [{"type": "text", "text": json.dumps({
                                         "kind": "answer_to_pending_question", "callId": "call_1",
                                         "answers": [{"id": "fmt", "selected": ["PDF"]}]})}]})
    state, late = one(state, "session/event", reply)
    assert [(e["type"], e["item_id"], e["data"]) for e in late] == [
        ("item.completed", "call_1", {"answers": [{"id": "fmt", "selected": ["PDF"]}], "late": True})]
    assert state.questions == frozenset()
    crashed, _ = one(S0, "session/event", ask("call_2"))
    _, unknown = one(crashed, "session/event", ask_result("call_2", "outcome unknown", is_error=True,
                                                           error={"code": "TOOL_OUTCOME_UNKNOWN"}))
    assert [e["data"] for e in unknown if e["kind"] == "input_request"] == [{"state": "continued"}], "宿主崩溃后补的结果：仍可答"


# 工具结果照本机宿主上实测的样子：文字是错误说明，官方的码在 error.code 里（不靠文字判断）
@pytest.mark.parametrize(("text", "error", "reason"), [
    ("Error: ask_user_question timed out before the user answered", {"name": "UserQuestionError", "code": "ASK_TIMED_OUT"},
     "timeout"),
    ("Error: ask_user_question was aborted before the user answered", {"name": "UserQuestionError", "code": "ASK_ABORTED"},
     "cancelled"),
    ("Error: timed out somewhere else", None, "cancelled"),
])
def test_failed_questions_close_the_card(text, error, reason):
    state, _ = one(S0, "session/event", ask())
    state, closed = one(state, "session/event", ask_result("call_1", text, is_error=True, error=error))
    assert [(e["type"], e["data"]) for e in closed if e["kind"] == "input_request"] == [("item.failed", {"reason": reason})]
    assert state.questions == frozenset()


def test_questions_asked_inside_ptc_wait_to_be_answered_later():
    """PTC 里发起的提问（官方 tool/ptc-dispatch，同官方投影）：只认已转入稍后再答的，出一张能稍后再答的卡片，迟到回答按 subCallId 收。"""
    pending = [{"type": "text", "text": json.dumps({"pending": True, "callId": "sub_1"})}]
    dispatched = session("tool/ptc-dispatch", {"name": "ask_user_question", "subCallId": "sub_1", "isError": False,
                                               "arguments": {"questions": QUESTIONS}, "content": pending})
    state, events = one(S0, "session/event", dispatched)
    assert [(e["type"], e["item_id"]) for e in events] == [("item.started", "sub_1"), ("item.updated", "sub_1")]
    assert events[1]["data"] == {"state": "continued"}
    assert "sub_1" in state.questions
    reply = session("user/message", {"source": {"kind": "user-question-reply", "callId": "sub_1", "outcome": "answered"},
                                     "content": [{"type": "text", "text": json.dumps({"answers": [{"id": "fmt", "selected": ["PDF"]}]})}]})
    state, late = one(state, "session/event", reply)
    assert [(e["type"], e["data"]) for e in late if e["kind"] == "input_request"] == [
        ("item.completed", {"answers": [{"id": "fmt", "selected": ["PDF"]}], "late": True})]
    for ignored in ({"name": "bash", "subCallId": "sub_2", "arguments": {}, "content": pending},
                    {"name": "ask_user_question", "subCallId": "sub_3", "isError": True, "arguments": {"questions": QUESTIONS},
                     "content": pending},
                    {"name": "ask_user_question", "subCallId": "sub_4", "arguments": {"questions": QUESTIONS},
                     "content": [{"type": "text", "text": json.dumps({"answers": []})}]}):
        assert one(S0, "session/event", session("tool/ptc-dispatch", ignored))[1] == [], ignored


def test_question_content_is_bounded_but_labels_are_kept_verbatim():
    long_label = "选" * 1001
    questions = [{"id": "q", "question": "问" * 3000, "options": [{"label": "甲"}, {"label": long_label}, {"x": 1}, "坏"]},
                 {"id": 5, "question": "id 不是字符串"}, {"id": "empty", "question": ""}, "坏"]
    state, events = one(S0, "session/event", ask("call_9", questions))
    [question] = next(e for e in events if e["kind"] == "input_request")["data"]["questions"]
    assert len(question["question"]) == 2000
    assert question["options"] == [{"label": "甲"}], "过长的选项整项丢掉，不截断标签"
    answers = [{"id": "q", "selected": ["甲", 3, long_label], "custom": "补" * 5000}, {"selected": []}]
    _, answered = one(state, "session/event", ask_result("call_9", {"answers": answers}))
    card = next(e for e in answered if e["kind"] == "input_request")
    assert card["data"]["answers"] == [{"id": "q", "selected": ["甲"], "custom": "补" * 4000}]
    assert card["data"]["late"] is False


def test_other_tool_results_and_unknown_calls_do_not_touch_questions():
    _, events = one(S0, "session/event", session("tool/call", {"callId": "c", "name": "ask_user_question", "arguments": "坏"}))
    assert all(e["kind"] != "input_request" for e in events), "读不出问题的调用不出卡片"
    _, events = one(S0, "session/event", ask_result("nobody", {"answers": []}))
    assert all(e["kind"] != "input_request" for e in events)


def test_todo_write_snapshots_one_item_per_run():
    state, _ = one(S0, "session/event", session("turn/start", {"turn": 4}))
    todos = [{"content": "读取合同", "status": "completed"}, {"content": "抽取条款", "status": "in_progress"},
             {"content": "坏状态", "status": "done"}, {"status": "pending"}, "坏"]
    _, events = one(state, "session/event", session("todo/write", {"todos": todos}))
    [event] = events
    assert (event["type"], event["kind"], event["item_id"], event["run_id"]) == ("item.updated", "todo", "todo-4", "run-4")
    assert event["data"] == {"items": [{"content": "读取合同", "status": "completed"},
                                       {"content": "抽取条款", "status": "in_progress"}]}
    _, cleared = one(state, "session/event", session("todo/write", {"todos": []}))
    assert cleared[0]["data"] == {"items": []}
    assert one(state, "session/event", session("todo/write", {"todos": "坏"}))[1] == []
    assert "todo" in ITEM_KINDS


def test_presented_deliverables_become_one_item_per_present():
    """官方 present 交付的文件（deliverables/presented）：网页与桌面都按它出交付卡片、按路径读（方案 7.7.3）。"""
    state, _ = one(S0, "session/event", session("turn/start", {"turn": 2}))
    files = [{"path": "/sessions/c1/out/报告.docx", "description": "季度报告"}, {"path": "/sessions/c1/a.csv"},
             {"description": "没有路径"}, {"path": ""}, "坏"]
    _, events = one(state, "session/event", session("deliverables/presented", {"files": files}), seq=11)
    [event] = events
    assert (event["type"], event["kind"], event["item_id"], event["run_id"]) == (
        "item.completed", "deliverable", "deliverable-11", "run-2")
    assert event["data"] == {"files": [{"path": "/sessions/c1/out/报告.docx", "description": "季度报告"},
                                       {"path": "/sessions/c1/a.csv"}]}
    assert one(state, "session/event", session("deliverables/presented", {"files": []}))[1] == []
    assert one(state, "session/event", session("deliverables/presented", {"files": "坏"}))[1] == []
    assert "deliverable" in ITEM_KINDS


@pytest.mark.parametrize("extracted", [
    '[Markdown extracted from "合同 \\"终版\\".docx" by the document parser, saved at "/workspace/input/x.md".]',
    # 解析还没完成时的那句（runtime-host attachment-handles.mjs 的 pendingText），同一个开头
    ('[Markdown extracted from "合同 \\"终版\\".docx" by the document parser is not ready yet: parsing is still '
     'running and the result will be saved at "/workspace/input/x.md".]'),
    # 给模型读的全文或目录（contentsText）：整篇文档不能出现在用户的气泡里
    '[Contents of "合同 \\"终版\\".docx" (12 characters, the text of "/workspace/input/x.md"), included in full.]\n付款 30 天',
])
def test_attachment_handles_become_attachments_not_text(extracted):
    """宿主在用户原话后附的文件说明块（逐字照官方 fileHandleText）不进显示文字，换成附件列表；路径不给浏览器。"""
    state, _ = project(ProjectionState(), "session/event", session("turn/start", {"turn": 1}), seq=1)
    handle = ('[File "合同 \\"终版\\".docx" (2048 bytes, sha256:aaaaaaaa): verbatim read-only copy saved at '
              '"/workspace/input/合同 \\"终版\\".docx". Read that path with your file tools when its contents are needed.]')
    message = {"id": "u1", "source": {"kind": "user"},
               "content": [{"type": "text", "text": "帮我改付款条款"}, {"type": "text", "text": handle},
                           {"type": "text", "text": extracted}]}
    _, events = project(state, "session/event", session("user/message", message), seq=2)
    data = events[0]["data"]
    assert data == {"role": "user", "text": "帮我改付款条款", "attachments": [{"name": '合同 "终版".docx', "size": 2048}]}
    assert "/workspace" not in str(events)


def test_mention_block_becomes_chips_not_text():
    """宿主写的 @ 说明块不进气泡文字，换成小标签数据（类型、名字、所在知识库，扩展的另带类型标识与类型名）；
    格式不对的项丢掉。"""
    items = [{"type": "document", "id": "kb1/d1", "name": "差旅办法.pdf", "base": "builtin.kb1", "baseName": "制度库"},
             {"type": "mcp", "id": "context7", "name": "Context7"},
             {"type": "skill", "id": "weekly-report", "name": "周报"}, {"type": "unknown", "id": "x", "name": "x"},
             {"type": "tag", "name": "没有 id"},
             {"type": "extension", "id": "p1", "name": "销售库", "kind": "data_source", "label": "数据源"}]
    block = "[用户这一轮 @ 点名的范围] " + json.dumps(items, ensure_ascii=False) + "\nknowledge_search 已由系统限定……\n用户点名的技能： /weekly-report "
    message = {"source": {"kind": "user"}, "id": "u1",
               "content": [{"type": "text", "text": "报销标准"}, {"type": "text", "text": block}]}
    _, [event] = one(S0, "session/event", session("user/message", message))
    assert event["data"]["text"] == "报销标准"
    assert event["data"]["mentions"] == [
        {"type": "document", "id": "kb1/d1", "name": "差旅办法.pdf", "base": "builtin.kb1", "base_name": "制度库"},
        {"type": "mcp", "id": "context7", "name": "Context7"}, {"type": "skill", "id": "weekly-report", "name": "周报"},
        {"type": "extension", "id": "p1", "name": "销售库", "kind": "data_source", "label": "数据源"}]
