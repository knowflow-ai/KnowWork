"""运行调试视图的整理逻辑：按轮切分、步骤时间线、用量对账、子 Agent、还原每步输入。"""

from knowwork_server.debugview.blocks import TEXT_LIMIT, blocks_of
from knowwork_server.debugview.context import APPROXIMATE, COMPACTED, step_input
from knowwork_server.debugview.digest import detail, summarize
from knowwork_server.debugview.events import main_events, slice_turns

from .logs import LOG, USAGE, frame

USAGE_MAP = {(u.turn, u.step): u for u in USAGE}


def test_turns_are_cut_at_turn_boundaries_and_input_before_a_turn_belongs_to_it():
    first, second = slice_turns(LOG)
    assert [first.turn, second.turn] == [1, 2]
    assert first.raw[0].seq == 0 and first.raw[-1].seq == 22, "开始前收到的输入算这一轮"
    assert second.raw[0].seq == 23


def test_summary_counts_steps_failures_and_cost_and_takes_the_users_own_words():
    first, second = slice_turns(LOG)
    summary = summarize(first, USAGE_MAP)
    assert (summary.status, summary.request, summary.steps, summary.tool_calls) == ("completed", "你好", 2, 1)
    assert summary.errors == 2, "一次失败的模型调用加一次重试"
    assert (summary.input_tokens, summary.output_tokens, summary.cost) == (250, 25, "0.0012")
    assert summary.duration_ms == 3000 - 1001
    running = summarize(second, USAGE_MAP)
    assert (running.status, running.request, running.duration_ms, running.cost) == ("running", "再来一次", None, None)


def test_detail_has_the_step_timeline_tool_results_usage_subagents_and_side_events():
    turn = detail(slice_turns(LOG)[0], USAGE_MAP)
    one, two = turn.steps
    assert (one.reasoning, one.text, one.duration_ms) == ("先看看目录", "我来查", 500)
    call = one.tool_calls[0]
    assert (call.name, call.arguments, call.result, call.is_error, call.duration_ms) == (
        "bash", '{"cmd":"ls"}', "a.txt", False, 300)
    assert [f for f in one.failures] == ["模型调用失败：504 超时", "第 1 次重试：504 超时"]
    assert (one.usage.model, one.usage.cost, one.usage.duration_ms) == ("default", "0.0012", 900)
    assert two.text == "完成" and two.usage.cost is None
    (kid,) = turn.subagents
    assert (kid.label, [c.name for s in kid.steps for c in s.tool_calls]) == ("子任务", ["read"])
    assert turn.events == ("审批 write：本会话内同类操作都批准", "加载技能 report 1.0.0")


def test_step_input_is_rebuilt_from_the_log_up_to_that_step():
    events = main_events(LOG)
    second = step_input(events, 1, 2)
    assert second.found and second.system == ("你是助手",)
    assert [t.name for t in second.tools] == ["bash"]
    assert [(m.role, m.source) for m in second.messages] == [
        ("user", "user"), ("user", "runtime-context"), ("assistant", None), ("tool", None)]
    assert second.messages[2].blocks[2].name == "bash" and second.messages[3].tool_call_id == "call1"
    assert second.notes == (APPROXIMATE,)
    assert [m.role for m in step_input(events, 1, 1).messages] == ["user", "user"]
    assert not step_input(events, 9, 9).found


def test_compaction_before_the_step_is_called_out():
    events = main_events((*LOG[:4], frame(4, "compaction/applied", {}), *LOG[5:]))
    assert COMPACTED in step_input(events, 1, 2).notes


def test_long_text_is_cut_and_marked():
    (block,) = blocks_of({"content": [{"type": "text", "text": "甲" * (TEXT_LIMIT + 5)}]})
    assert len(block.text) == TEXT_LIMIT and block.truncated


def test_request_replaces_the_hosts_attachment_note_with_file_names():
    note = ('[File "季度报告.pdf" (2048 bytes, sha256:0123abcd): verbatim read-only copy saved at '
            '"/workspace/.attachments/1/a.pdf". Read that path with your file tools when its contents are needed.]')
    message = {"role": "user", "content": [{"type": "text", "text": "\n\n总结一下\n"}, {"type": "text", "text": note}]}
    log = (frame(0, "user/message", message), frame(1, "turn/start", {"turn": 1}))
    summary = summarize(slice_turns(log)[0], {})
    assert summary.request == "总结一下（附件：季度报告.pdf）"
    only_file = {"role": "user", "content": [{"type": "text", "text": note}]}
    log = (frame(0, "user/message", only_file), frame(1, "turn/start", {"turn": 1}))
    assert summarize(slice_turns(log)[0], {}).request == "附件：季度报告.pdf"
