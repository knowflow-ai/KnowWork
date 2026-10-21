"""宿主事件 → 用量记录（纯函数），以及每轮调用次数上限。"""

from datetime import UTC, datetime

import pytest

from knowwork_server.models.usage import (
    TurnCallBudget,
    UsageContext,
    model_from_header,
    stream_duration_ms,
    usage_from_event,
)

CTX = UsageContext(workspace_id="ws1", user_id="alice", conversation_id="c1", model_alias="default")
AT = datetime(2026, 10, 18, 12, tzinfo=UTC)


def _event(**overrides):
    data = {"turn": 2, "step": 1, "message": {"content": "回答原文"},
            "stream": [{"type": "text-chunks", "time0": 1000, "index": 0, "dt": [100, 150], "texts": ["a", "b"]},
                       {"type": "chunk", "time": 1400, "chunk": {"type": "usage"}}],
            "usage": {"inputTokens": 120, "outputTokens": 30, "totalTokens": 150, "cacheReadTokens": 64,
                      "reasoningTokens": 5}}
    data.update(overrides)
    return data


def test_converts_assistant_message():
    entry = usage_from_event("assistant/message", _event(), CTX, AT)
    assert (entry.turn, entry.step, entry.input_tokens, entry.output_tokens, entry.total_tokens) == (2, 1, 120, 30, 150)
    assert (entry.cache_read_tokens, entry.reasoning_tokens, entry.duration_ms, entry.status) == (64, 5, 400, "ok")
    assert (entry.workspace_id, entry.user_id, entry.conversation_id, entry.model_alias) == ("ws1", "alice", "c1",
                                                                                            "default")
    assert "回答原文" not in repr(entry), "不应带上回答原文"


def test_total_defaults_to_sum_and_optional_counters_to_zero():
    entry = usage_from_event("assistant/message", _event(usage={"inputTokens": 7, "outputTokens": 3}), CTX, AT)
    assert (entry.total_tokens, entry.cache_read_tokens, entry.reasoning_tokens) == (10, 0, 0)


def test_interrupted_marks_status():
    assert usage_from_event("assistant/message", _event(interrupted=True), CTX, AT).status == "interrupted"


@pytest.mark.parametrize("kind,data", [
    ("tool/call", _event()),
    ("assistant/message", "not a dict"),
    ("assistant/message", _event(usage=None)),
    ("assistant/message", _event(turn="2")),
    ("assistant/message", _event(step=-1)),
    ("assistant/message", _event(usage={"inputTokens": -1, "outputTokens": 1})),
    ("assistant/message", _event(usage={"inputTokens": True, "outputTokens": 1})),
    ("assistant/message", _event(usage={"inputTokens": float("nan"), "outputTokens": 1})),
    ("assistant/message", _event(usage={"inputTokens": "9", "outputTokens": 1})),
])
def test_ignores_non_usage_or_malformed(kind, data):
    assert usage_from_event(kind, data, CTX, AT) is None


@pytest.mark.parametrize("stream,expected", [(None, None), ([], None), (["x", {"type": "?"}], None),
                                             ([{"time": 5}], 0), ([{"time0": 10, "dt": "bad"}, {"time": 30}], 20)])
def test_stream_duration(stream, expected):
    assert stream_duration_ms(stream) == expected


def test_model_from_header():
    assert model_from_header("request/header", {"config": {"model": "qwen-max"}}) == "qwen-max"
    assert model_from_header("request/header", {"config": {"model": ""}}) is None
    assert model_from_header("request/header", {"config": "x"}) is None
    assert model_from_header("step/start", {"config": {"model": "a"}}) is None


def test_turn_call_budget_counts_steps_per_turn():
    budget = TurnCallBudget(limit=2)
    for _ in range(2):
        budget = budget.after("step/start", {"turn": 1, "step": 0})
    assert budget.calls == 2 and not budget.exceeded
    over = budget.after("step/start", {"turn": 1, "step": 2})
    assert over.exceeded and not budget.exceeded, "必须返回新对象，不改原对象"
    assert not over.after("step/start", {"turn": 2, "step": 0}).exceeded, "新的一轮重新计数"
    assert over.after("assistant/message", {}) is over
    assert over.after("step/start", {"turn": "x"}) is over


def test_unlimited_budget():
    budget = TurnCallBudget(limit=0)
    for step in range(100):
        budget = budget.after("step/start", {"turn": 1, "step": step})
    assert not budget.exceeded
