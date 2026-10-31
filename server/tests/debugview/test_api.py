"""运行调试视图的接口：真实数据库里的会话日志与用量，只有会话本人能看，键名是驼峰式，能导出原始日志。"""

from datetime import date

import httpx
import pytest
from fastapi import FastAPI

from knowwork_server.conversations.api import Actor
from knowwork_server.conversations.models import Conversation, RunEvent
from knowwork_server.conversations.service import ConversationNotFound
from knowwork_server.debugview import build_debugview, build_router
from knowwork_server.models.recorder import UsageRecorder
from knowwork_server.models.tables import UsageRecord

from .logs import AT, LOG, USAGE

CONV = "/api/workspaces/default/conversations"

pytestmark = pytest.mark.anyio


class Lookup:
    def __init__(self, conversation_id):
        self.conversation_id = conversation_id

    async def get(self, *, conversation_id, workspace_id, user_id):
        if conversation_id != self.conversation_id or user_id != "dev-user":
            raise ConversationNotFound(conversation_id)
        return Conversation(id=conversation_id, workspace_id="default", user_id=user_id, title="")


def actor_for(user):
    async def dependency():
        return Actor(user_id=user, workspace_id="default")

    return dependency


@pytest.fixture
async def seeded(sessionmaker, conversation_id):
    async with sessionmaker() as session:
        session.add_all(RunEvent(conversation_id=conversation_id, seq=e.seq, workspace_id="default", host_epoch="e1",
                                 host_cursor=e.seq, kind=e.kind, data=e.data, ui=[]) for e in LOG)
        session.add_all(UsageRecord(workspace_id="default", user_id="dev-user", conversation_id=conversation_id,
                                    turn=u.turn, step=u.step, model_alias=u.model_alias, input_tokens=u.input_tokens,
                                    output_tokens=u.output_tokens, total_tokens=u.input_tokens + u.output_tokens,
                                    cost=u.cost, duration_ms=u.duration_ms, status=u.status, occurred_at=AT,
                                    day=date(2026, 10, 20)) for u in USAGE)
        await session.commit()
    return conversation_id


class Traces:
    """业务按 callId 另记的过程（知识检索记录满足同一个接口）。"""

    def __init__(self, found=None):
        self.found = found or {}
        self.asked: list[str] = []

    async def traces(self, workspace_id, conversation_id, call_ids):
        self.asked = list(call_ids)
        return {k: v for k, v in self.found.items() if k in call_ids}


def client(sessionmaker, conversation_id, user="dev-user", traces=None):
    app = FastAPI()
    debug = build_debugview(sessionmaker, UsageRecorder(sessionmaker), traces)
    app.include_router(build_router(debug, Lookup(conversation_id), actor_for(user)))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://kw")


async def test_owner_sees_turns_steps_and_reconstructed_input(sessionmaker, seeded):
    base = f"{CONV}/{seeded}/debug"
    async with client(sessionmaker, seeded) as http:
        listing = (await http.get(f"{base}/turns")).json()
        assert listing["truncated"] is False
        assert [(t["turn"], t["status"], t["toolCalls"], t["cost"]) for t in listing["turns"]] == [
            (1, "completed", 1, "0.0012"), (2, "running", 0, None)]
        turn = (await http.get(f"{base}/turns/1")).json()
        assert turn["steps"][0]["toolCalls"][0]["durationMs"] == 300
        assert turn["steps"][0]["usage"]["inputTokens"] == 100
        assert turn["subagents"][0]["label"] == "子任务"
        assert turn["background"] == [], "没有扩展补的上下文"
        step_input = (await http.get(f"{base}/turns/1/steps/2/input")).json()
        assert [m["role"] for m in step_input["messages"]] == ["user", "user", "assistant", "tool"]
        assert step_input["messages"][3]["toolCallId"] == "call1"
        assert (await http.get(f"{base}/turns/9")).status_code == 404
        assert (await http.get(f"{base}/turns/1/steps/9/input")).status_code == 404


async def test_export_downloads_the_raw_frames_of_one_turn(sessionmaker, seeded):
    async with client(sessionmaker, seeded) as http:
        response = await http.get(f"{CONV}/{seeded}/debug/turns/1/export")
    assert response.headers["content-disposition"] == f'attachment; filename="{seeded}-turn-1.json"'
    body = response.json()
    assert [e["seq"] for e in body["events"]] == list(range(23))
    assert body["events"][14]["kind"] == "subagent/event", "子 Agent 的帧一起导出"


async def test_other_users_get_404(sessionmaker, seeded):
    async with client(sessionmaker, seeded, user="someone-else") as http:
        assert (await http.get(f"{CONV}/{seeded}/debug/turns")).status_code == 404


async def test_tool_calls_carry_the_trace_recorded_by_call_id(sessionmaker, seeded):
    """知识检索这类工具的过程不在会话日志里，按 callId 从业务记录取来挂上；子 Agent 的调用也一样。"""
    trace = [{"label": "视觉检索", "summary": "命中 1 页", "lines": ["手册 第 3 页"], "source": "扩展知识库"}]
    traces = Traces({"call1": trace, "k1": trace})
    async with client(sessionmaker, seeded, traces=traces) as http:
        turn = (await http.get(f"{CONV}/{seeded}/debug/turns/1")).json()
    assert set(traces.asked) >= {"call1", "k1"}
    assert turn["steps"][0]["toolCalls"][0]["trace"] == trace
    assert turn["subagents"][0]["steps"][0]["toolCalls"][0]["trace"] == trace
