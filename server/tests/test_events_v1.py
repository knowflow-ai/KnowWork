"""events.v1 映射：DSH 原始会话事件与插件帧 → 前端事件。纯函数，逐类覆盖。"""

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
    _, events = one(state, "approval/requested", {"id": "a1", "toolName": "kw_write", "callId": "c1", "reason": "写入"})
    assert [e["type"] for e in events] == ["item.started", "run.waiting"]
    assert all(e["run_id"] == "run-2" for e in events)
    assert events[0]["kind"] == "approval" and events[0]["item_id"] == "a1"
    assert events[0]["data"] == {"tool_name": "kw_write", "call_id": "c1", "reason": "写入"}
    assert events[1]["data"] == {"reason": "approval"}


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


def test_approval_resolved_and_subagent():
    _, resolved = one(S0, "approval/resolved", {"id": "a1", "outcome": "rejected"})
    assert (resolved[0]["type"], resolved[0]["kind"], resolved[0]["data"]) == ("item.completed", "approval",
                                                                               {"outcome": "rejected"})
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
    ("approval/requested", {"no": "id"}),
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
    for source in ("runtime-context", "agent-message", "subagent-settled"):
        injected = {**mine, "id": "x", "source": {"kind": source}}
        assert project(state, "session/event", session("user/message", injected), seq=3)[1] == []
    empty = {**mine, "content": [{"type": "image", "url": "x"}]}
    assert project(state, "session/event", session("user/message", empty), seq=4)[1] == []


def test_question_frames_become_one_input_request_item():
    state, _ = one(S0, "session/event", session("turn/start", {"turn": 2}))
    questions = [{"id": "fmt", "question": "导出成什么格式？", "header": "格式", "multiSelect": False,
                  "options": [{"label": "Excel (Recommended)", "description": "表格"}, {"label": "PDF"}]}]
    _, asked = one(state, "question/requested", {"id": "call_1", "sessionId": "c1", "questions": questions, "timed": True})
    assert [(e["type"], e["kind"], e["item_id"], e["run_id"]) for e in asked] == [
        ("item.started", "input_request", "call_1", "run-2"), ("run.waiting", None, "call_1", "run-2")]
    assert asked[0]["data"] == {"timed": True, "questions": [{
        "id": "fmt", "question": "导出成什么格式？", "header": "格式", "detail": None, "multi_select": False,
        "options": [{"label": "Excel (Recommended)", "description": "表格"}, {"label": "PDF"}]}]}
    assert asked[1]["data"] == {"reason": "input"}

    _, answered = one(state, "question/answered", {"id": "call_1", "answers": [{"id": "fmt", "selected": ["PDF"]}],
                                                   "late": True})
    assert (answered[0]["type"], answered[0]["kind"], answered[0]["item_id"]) == ("item.completed", "input_request", "call_1")
    assert answered[0]["data"] == {"answers": [{"id": "fmt", "selected": ["PDF"]}], "late": True}

    _, continued = one(state, "question/continued", {"id": "call_1"})
    assert (continued[0]["type"], continued[0]["data"]) == ("item.updated", {"state": "continued"})
    for reason, expected in (("timeout", "timeout"), ("cancelled", "cancelled"), ("interrupted", "interrupted"),
                             ("weird", "cancelled")):
        _, closed = one(state, "question/closed", {"id": "call_1", "reason": reason})
        assert (closed[0]["type"], closed[0]["data"]) == ("item.failed", {"reason": expected})


def test_question_content_is_bounded_but_labels_are_kept_verbatim():
    long_label = "选" * 1001
    questions = [{"id": "q", "question": "问" * 3000, "options": [{"label": "甲"}, {"label": long_label}, {"x": 1}, "坏"]},
                 {"id": 5, "question": "id 不是字符串"}, {"id": "empty", "question": ""}, "坏"]
    _, events = one(S0, "question/requested", {"id": "call_9", "questions": questions})
    [question] = events[0]["data"]["questions"]
    assert len(question["question"]) == 2000
    assert question["options"] == [{"label": "甲"}], "过长的选项整项丢掉，不截断标签"
    answers = [{"id": "q", "selected": ["甲", 3, long_label], "custom": "补" * 5000}, {"selected": []}]
    _, answered = one(S0, "question/answered", {"id": "call_9", "answers": answers})
    assert answered[0]["data"]["answers"] == [{"id": "q", "selected": ["甲"], "custom": "补" * 4000}]
    assert answered[0]["data"]["late"] is False


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


@pytest.mark.parametrize("kind", ["question/requested", "question/answered", "question/continued", "question/closed"])
def test_question_frames_without_id_are_dropped(kind):
    assert one(S0, kind, {"questions": []})[1] == []


def test_attachment_handles_become_attachments_not_text():
    """宿主在用户原话后附的文件说明块（逐字照官方 fileHandleText）不进显示文字，换成附件列表；路径不给浏览器。"""
    state, _ = project(ProjectionState(), "session/event", session("turn/start", {"turn": 1}), seq=1)
    handle = ('[File "合同 \\"终版\\".docx" (2048 bytes, sha256:aaaaaaaa): verbatim read-only copy saved at '
              '"/workspace/input/合同 \\"终版\\".docx". Read that path with your file tools when its contents are needed.]')
    extracted = '[Markdown extracted from "合同 \\"终版\\".docx" by the document parser, saved at "/workspace/input/x.md".]'
    message = {"id": "u1", "source": {"kind": "user"},
               "content": [{"type": "text", "text": "帮我改付款条款"}, {"type": "text", "text": handle},
                           {"type": "text", "text": extracted}]}
    _, events = project(state, "session/event", session("user/message", message), seq=2)
    data = events[0]["data"]
    assert data == {"role": "user", "text": "帮我改付款条款", "attachments": [{"name": '合同 "终版".docx', "size": 2048}]}
    assert "/workspace" not in str(events)
