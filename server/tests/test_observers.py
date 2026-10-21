"""观察者组合与产物收取观察者：一个出错不影响其他；只在回合正常完成时收这一回合 present 交付的文件。"""

import pytest

from knowwork_server.artifacts.observer import ArtifactObserver
from knowwork_server.conversations.observers import fan_out
from knowwork_server.sandbox.contract import SandboxError
from knowwork_server.sandbox.unavailable import UnavailableSandbox

pytestmark = pytest.mark.anyio


def test_fan_out_isolates_failures():
    seen = []

    def broken(*args):
        raise RuntimeError("boom")

    fan_out(broken, lambda *args: seen.append(args))("c1", "ws", "session/event", {})
    assert seen == [("c1", "ws", "session/event", {})]


class Collector:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    async def collect(self, workspace_id, session_id, *, run_id=None, paths=()):
        self.calls.append((workspace_id, session_id, run_id, tuple(paths)))
        if self.fail:
            raise RuntimeError("sandbox down")

        class Result:
            message = "有 1 个文件超过上限"

        return Result()


def turn_end(kind, turn=3):
    return {"type": "turn/end", "data": {"turn": turn, "reason": {"kind": kind}}}


def presented(*paths, turn=3):
    return {"type": "deliverables/presented", "data": {"turn": turn, "callId": "c", "files": [{"path": p} for p in paths]}}


async def test_collects_only_after_completed_turns():
    collector = Collector()
    observer = ArtifactObserver(collector)
    observer("c1", "ws", "session/event", presented("/sessions/c1/half.docx", turn=2))
    observer("c1", "ws", "session/event", turn_end("aborted", turn=2))  # 被取消的回合：交付记录丢掉
    observer("c1", "ws", "agent/status", {"status": "idle"})
    observer("c1", "ws", "session/event", {"type": "turn/start", "data": {"turn": 3}})
    observer("c1", "ws", "session/event", presented("/sessions/c1/a.docx", "/sessions/c1/b.xlsx"))
    observer("c1", "ws", "session/event", presented("/sessions/c1/c.md"))
    observer("c1", "ws", "session/event", turn_end("completed"))
    observer("c1", "ws", "session/event", turn_end("completed", turn=4))  # 没交付的回合不收
    await observer.drain()
    assert collector.calls == [("ws", "c1", "run-3", ("/sessions/c1/a.docx", "/sessions/c1/b.xlsx", "/sessions/c1/c.md"))]


async def test_collect_failure_is_logged_not_raised(caplog):
    observer = ArtifactObserver(Collector(fail=True))
    observer("c1", "ws", "session/event", presented("/sessions/c1/a.txt"))
    observer("c1", "ws", "session/event", turn_end("completed"))
    await observer.drain()
    assert any("collecting artifacts" in r.message for r in caplog.records)


async def test_unavailable_sandbox_refuses_inputs_and_outputs():
    sandbox = UnavailableSandbox()
    with pytest.raises(SandboxError, match="没有启用会话沙箱"):
        await sandbox.put_input("ws", "c1", "a.txt", b"x")
    with pytest.raises(SandboxError, match="没有启用会话沙箱"):
        await sandbox.read_delivered("ws", "c1", "/workspace/a.txt", max_bytes=10)
    await sandbox.destroy("ws", "c1")


class GatedCollector:
    """collect 卡在 gate 上，直到测试放行。"""

    def __init__(self):
        import asyncio

        self.gate, self.calls = asyncio.Event(), []

    async def collect(self, workspace_id, session_id, *, run_id=None, paths=()):
        self.calls.append(session_id)
        await self.gate.wait()

        class Result:
            message = None

        return Result()


async def test_settle_waits_for_in_flight_collection_of_that_conversation():
    import asyncio

    collector = GatedCollector()
    observer = ArtifactObserver(collector)
    observer("c1", "ws", "session/event", presented("/sessions/c1/a.txt"))
    observer("c1", "ws", "session/event", turn_end("completed"))
    waiter = asyncio.create_task(observer.settle("c1"))
    await asyncio.sleep(0.01)
    assert not waiter.done(), "收取还没结束，settle 不应返回"
    await observer.settle("c2")  # 别的会话没有在收的产物，立即返回
    collector.gate.set()
    await asyncio.wait_for(waiter, 1)


async def test_settle_gives_up_after_timeout_without_raising():
    collector = GatedCollector()
    observer = ArtifactObserver(collector)
    observer("c1", "ws", "session/event", presented("/sessions/c1/a.txt"))
    observer("c1", "ws", "session/event", turn_end("completed"))
    await observer.settle("c1", timeout=0.01)  # 超时只是不再等，列表照常返回已有的
    collector.gate.set()
    await observer.drain()


async def test_settle_survives_collection_failure():
    observer = ArtifactObserver(Collector(fail=True))
    observer("c1", "ws", "session/event", presented("/sessions/c1/a.txt"))
    observer("c1", "ws", "session/event", turn_end("completed"))
    await observer.settle("c1")
