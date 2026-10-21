"""技能运行事实的投影：从一轮会话日志算出每个技能的运行、步骤、用量与失败阶段（纯函数）。"""

from datetime import UTC, datetime
from decimal import Decimal

from knowwork_server.conversations.runlog import RawEvent
from knowwork_server.debugview.events import slice_turns
from knowwork_server.models.recorder import StepUsage
from knowwork_server.skills.facts import project_turn

AT = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)


def frame(seq, kind, data, time=None):
    return RawEvent(seq=seq, kind="session/event",
                    data={"seq": seq, "type": kind, "time": time if time is not None else 1000 + seq * 10,
                          "data": data}, at=AT)


def child(seq, kind, data):
    return RawEvent(seq=seq, kind="subagent/event",
                    data={"sessionId": "kid", "event": {"seq": seq, "type": kind, "time": 1000 + seq * 10,
                                                        "data": data}}, at=AT)


def host(seq, kind, data):
    return RawEvent(seq=seq, kind=kind, data=data, at=AT)


def loaded(seq, skill="report", *, mode="automatic", session="root", version="1.0.0"):
    return host(seq, "skill/loaded", {"sessionId": session, "skill": skill, "version": version, "mode": mode,
                                      "unlocked": []})


def call(seq, call_id, name, step=1):
    return frame(seq, "tool/call", {"turn": 1, "step": step, "callId": call_id, "name": name, "arguments": "{}"})


def result(seq, call_id, *, error=False, step=1):
    return frame(seq, "tool/result", {"turn": 1, "step": step, "message": {
        "role": "tool", "toolCallId": call_id, "isError": error, "content": [{"type": "text", "text": "x"}]}})


def end(seq, kind="completed", **extra):
    return frame(seq, "turn/end", {"turn": 1, "reason": {"kind": kind, **extra}})


def usage(step, tokens=(100, 10), cost="0.001"):
    return StepUsage(turn=1, step=step, model_alias="default", input_tokens=tokens[0], output_tokens=tokens[1],
                     cost=Decimal(cost) if cost else None, duration_ms=100, status="ok")


USAGE = (usage(1), usage(2, (200, 20), "0.002"), usage(3, (300, 30), None))


def project(events, records=USAGE, artifacts=0):
    (turn,) = slice_turns(events)
    return project_turn(turn, records, artifacts)


def test_model_loaded_skill_counts_steps_and_usage_from_the_loading_step():
    (run,) = project([
        frame(1, "turn/start", {"turn": 1}),
        frame(2, "step/start", {"turn": 1, "step": 1}),
        call(3, "c0", "bash"), result(4, "c0"),
        frame(5, "step/start", {"turn": 1, "step": 2}),
        call(6, "s1", "skill"), result(7, "s1"),
        loaded(8),
        call(9, "c1", "extract_records", step=2), result(10, "c1", step=2),
        frame(11, "step/start", {"turn": 1, "step": 3}),
        call(12, "c2", "docx_render", step=3),
        end(13),
    ])
    assert (run.skill, run.version, run.selection_mode, run.status) == ("report", "1.0.0", "automatic", "succeeded")
    # 加载之前的 bash 与加载技能本身的 skill 调用都不算；没等到结果的调用记为跳过
    assert [(s.tool_name, s.status, s.duration_ms) for s in run.steps] == [
        ("extract_records", "succeeded", 10), ("docx_render", "skipped", 0)]
    # 模型决定加载的那一步（2）起算：第 1 步不算，费用为空的那一步只计 token
    assert (run.total_tokens, run.cost) == (220 + 330, Decimal("0.002"))
    # 开始取加载帧的时间（宿主帧没有 time，用落库时间），结束取 turn/end
    assert (run.started_ms, run.ended_ms) == (int(AT.timestamp() * 1000), 1130)


def test_user_invoked_skill_before_first_step_takes_the_whole_turn():
    (run,) = project([
        frame(1, "turn/start", {"turn": 1}),
        loaded(2, mode="manual"), loaded(3, mode="manual"),  # 同一会话重复加载只算一次
        frame(4, "step/start", {"turn": 1, "step": 1}),
        end(5),
    ])
    assert run.selection_mode == "manual"
    assert (run.total_tokens, run.cost) == (110 + 220 + 330, Decimal("0.003"))


def test_gate_failure_blames_the_last_failed_step():
    def gated(*steps):
        events = [frame(1, "turn/start", {"turn": 1}), loaded(2), *steps,
                  host(40, "skill-gate/failed", {"turn": 1, "skills": ["report"], "nudges": 2,
                                                 "missing": [{"skill": "report", "tools": ["docx_render"]}]}),
                  end(41)]
        (run,) = project(events)
        return run.status, run.failure_stage, run.failure_code

    assert gated() == ("failed", "step", "completion_missing")
    assert gated(call(3, "c1", "knowledge_search"), result(4, "c1", error=True)) == (
        "failed", "knowledge", "completion_missing")
    assert gated(call(3, "c1", "knowledge_search"), result(4, "c1", error=True),
                 call(5, "c2", "docx_render"), result(6, "c2", error=True)) == ("failed", "tool", "completion_missing")
    rejected = (call(3, "c1", "send_mail"), frame(4, "approval/asked", {"id": "a1", "toolName": "send_mail",
                                                                         "callId": "c1"}),
                frame(5, "approval/decided", {"id": "a1", "outcome": "rejected"}), result(6, "c1", error=True))
    assert gated(*rejected) == ("failed", "permission", "completion_missing")


def test_turn_end_reasons_map_to_status_and_stage():
    def ended(*reason):
        kind, extra = reason[0], reason[1] if len(reason) > 1 else {}
        (run,) = project([frame(1, "turn/start", {"turn": 1}), loaded(2), end(3, kind, **extra)])
        return run.status, run.failure_stage, run.failure_code

    assert ended("aborted", {"reason": {"kind": "user"}}) == ("cancelled", None, None)
    assert ended("interrupted") == ("cancelled", None, None)
    assert ended("error", {"error": {"code": "RATE_LIMIT"}}) == ("failed", "generation", "RATE_LIMIT")
    assert ended("error") == ("failed", "generation", "model_error")
    assert ended("max-tokens") == ("failed", "generation", "max_tokens")
    assert ended("blocked") == ("failed", "permission", "blocked")


def test_presented_files_must_be_collected():
    events = [frame(1, "turn/start", {"turn": 1}), loaded(2),
              frame(3, "deliverables/presented", {"files": [{"path": "out/报告.docx"}]}), end(4)]
    (missing,) = project(events, artifacts=0)
    assert (missing.status, missing.failure_stage, missing.failure_code) == ("failed", "delivery", "artifact_missing")
    (delivered,) = project(events, artifacts=2)
    assert (delivered.status, delivered.artifact_count) == ("succeeded", 2)
    # 交付发生在加载之前：产物不算这个技能的
    (early,) = project([frame(1, "turn/start", {"turn": 1}),
                        frame(2, "deliverables/presented", {"files": [{"path": "a.txt"}]}), loaded(3), end(4)],
                       artifacts=1)
    assert (early.status, early.artifact_count) == ("succeeded", 0)


def test_subagent_skill_uses_its_own_steps_and_is_not_gated():
    (run,) = project([
        frame(1, "turn/start", {"turn": 1}),
        child(2, "step/start", {"turn": 1, "step": 1}),
        loaded(3, "xlsx-report", session="kid"),
        child(4, "tool/call", {"turn": 1, "step": 1, "callId": "k1", "name": "xlsx_render", "arguments": "{}"}),
        child(5, "tool/result", {"turn": 1, "step": 1, "message": {"role": "tool", "toolCallId": "k1",
                                                                    "isError": True, "content": []}}),
        call(6, "c1", "bash"), result(7, "c1"),  # 主会话的调用不属于子 Agent 加载的技能
        host(8, "skill-gate/failed", {"turn": 1, "missing": [{"skill": "xlsx-report", "tools": ["xlsx_render"]}]}),
        end(9),
    ])
    assert (run.session_id, run.status, run.total_tokens, run.cost) == ("kid", "succeeded", 0, Decimal(0))
    assert [(s.tool_name, s.status) for s in run.steps] == [("xlsx_render", "failed")]


def test_two_skills_in_one_turn_and_unfinished_turns():
    events = [frame(1, "turn/start", {"turn": 1}), loaded(2, "report"), call(3, "c1", "docx_render"),
              loaded(4, "summary"), call(5, "c2", "bash"), result(6, "c1"), result(7, "c2"), end(8)]
    first, second = project(events)
    assert [s.tool_name for s in first.steps] == ["docx_render", "bash"]
    assert [s.tool_name for s in second.steps] == ["bash"]
    # 没收到 turn/end 的轮次不投影；没加载技能的轮次也没有运行
    assert project(events[:-1]) == ()
    assert project([frame(1, "turn/start", {"turn": 1}), end(2)]) == ()
    # 字段不全的加载帧忽略
    assert project([frame(1, "turn/start", {"turn": 1}), host(2, "skill/loaded", {"skill": "x"}), end(3)]) == ()
