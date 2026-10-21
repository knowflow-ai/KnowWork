"""用量观察者：从事件流记用量（去重、带上会话的用户与模型别名），单轮调用超限时取消本轮。"""

import asyncio

import pytest

from knowwork_server.models.observer import UsageObserver

pytestmark = pytest.mark.anyio


def event(type_, data):
    return {"type": type_, "seq": 1, "time": 1, "data": data}


def message(turn, step, inputs=10, outputs=5):
    return event("assistant/message", {"turn": turn, "step": step, "message": {"content": "原文不应被保存"},
                                       "usage": {"inputTokens": inputs, "outputTokens": outputs}})


class Recorder:
    def __init__(self):
        self.entries = []

    async def record(self, entry):
        self.entries.append(entry)
        return True


async def owner_of(conversation_id):
    return {"c1": "alice"}.get(conversation_id)


async def settle():
    for _ in range(5):
        await asyncio.sleep(0)


async def test_records_usage_with_user_and_model_alias():
    recorder, cancelled = Recorder(), []

    async def cancel(cid):
        cancelled.append(cid)

    observer = UsageObserver(recorder, owner_of, cancel, default_model="default", max_calls_per_turn=0)
    observer("c1", "default", "session/event", event("request/header", {"config": {"model": "kw-chat"}}))
    observer("c1", "default", "session/event", message(1, 1))
    observer("c1", "default", "agent/status", {"status": "idle"})
    await observer.drain()
    [entry] = recorder.entries
    assert (entry.user_id, entry.model_alias, entry.turn, entry.step) == ("alice", "kw-chat", 1, 1)
    assert entry.input_tokens == 10 and entry.output_tokens == 5
    assert "原文" not in repr(entry)
    assert cancelled == []


async def test_falls_back_to_default_model_and_skips_unknown_conversations():
    recorder = Recorder()

    async def cancel(cid):
        pass

    observer = UsageObserver(recorder, owner_of, cancel, default_model="default", max_calls_per_turn=0)
    observer("c1", "default", "session/event", message(1, 1))
    observer("ghost", "default", "session/event", message(1, 1))
    await observer.drain()
    assert [e.model_alias for e in recorder.entries] == ["default"]


async def test_cancels_turn_once_when_call_budget_exceeded():
    recorder, cancelled = Recorder(), []

    async def cancel(cid):
        cancelled.append(cid)

    observer = UsageObserver(recorder, owner_of, cancel, default_model="default", max_calls_per_turn=2)
    for step in (1, 2, 3, 4):
        observer("c1", "default", "session/event", event("step/start", {"turn": 1, "step": step}))
    observer("c1", "default", "session/event", event("step/start", {"turn": 2, "step": 1}))
    await observer.drain()
    assert cancelled == ["c1"], "同一轮只取消一次，新一轮重新计数"


async def test_recorder_failure_is_logged_not_raised(caplog):
    class Broken:
        async def record(self, entry):
            raise RuntimeError("db down")

    async def cancel(cid):
        pass

    observer = UsageObserver(Broken(), owner_of, cancel, default_model="default", max_calls_per_turn=0)
    observer("c1", "default", "session/event", message(1, 1))
    await observer.drain()
    assert any("usage" in r.message for r in caplog.records)
