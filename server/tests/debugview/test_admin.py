"""管理端「运行记录」：全空间按时间列出每一轮，只有空间管理员能看；查看、导出别人的运行记审计。"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI, Header

from knowwork_server.access.service import AccessService
from knowwork_server.auth.principal import Principal
from knowwork_server.conversations.models import AnswerFeedback, Conversation, RunEvent
from knowwork_server.conversations.service import ConversationNotFound
from knowwork_server.conversations.summaries import RunSummaries
from knowwork_server.debugview import build_admin_router, build_debugview, build_run_records
from knowwork_server.models.recorder import UsageRecorder
from knowwork_server.models.tables import UsageRecord

from .logs import LOG, USAGE

pytestmark = pytest.mark.anyio
NOW = datetime.now(UTC)


@dataclass
class Profile:
    name: str
    email: str


class Finder:
    def __init__(self, conversations):
        self.conversations = {c.id: c for c in conversations}

    async def find(self, *, conversation_id, workspace_id):
        found = self.conversations.get(conversation_id)
        if found is None or found.workspace_id != workspace_id:
            raise ConversationNotFound(conversation_id)
        return found


class Sink:
    def __init__(self):
        self.events = []

    async def record(self, event):
        self.events.append(event)


async def profiles(user_ids):
    return {uid: Profile(name=f"{uid} 的名字", email=f"{uid}@corp.cn") for uid in user_ids}


async def summarize(sessionmaker, ws, logs):
    """样本帧经每轮汇总的观察者写进去（运行记录的列表读它）；帧上的时间挪到半小时前，落在默认的七天里。"""
    owners = {cid: owner for cid, (owner, _) in logs.items()}

    async def owner_of(conversation_id):
        return owners.get(conversation_id)

    async def knowledge(ended):
        # verdict 等几项本来是回合结束后判断时（knowledge.answer_check）补进来的，这里直接给
        return {"searches": 1, "cited": 0, "bases": ["builtin.kb1"], "cited_bases": [],
                "verdict": "unanswered", "missing": "请假流程", "reason": "kb_lacks"} if ended.turn == 1 else None

    summaries = RunSummaries(sessionmaker, owner_of)
    summaries.contribute("knowledge", knowledge)
    base = int((NOW - timedelta(minutes=30)).timestamp() * 1000)
    for cid, (_, events) in logs.items():
        for e in events:
            if isinstance(e.data, dict) and "time" in e.data:
                summaries(cid, ws, e.kind, {**e.data, "time": base + e.seq * 1000})
    await summaries.drain()


@pytest.fixture
async def env(sessionmaker):
    ws = "ws_" + uuid.uuid4().hex[:10]
    access = AccessService(sessionmaker)
    assert await access.reload()
    await access.create_workspace(workspace_id=ws, name="运行记录", owner_user_id="boss")
    await access.add_member("boss", ws, "alice", "editor")
    mine = Conversation(id="c_" + uuid.uuid4().hex, workspace_id=ws, user_id="alice", title="请假条")
    own = Conversation(id="c_" + uuid.uuid4().hex, workspace_id=ws, user_id="boss", title="自己的")
    async with sessionmaker() as session:
        session.add_all([mine, own])
        await session.flush()
        for conversation, events in ((mine, LOG), (own, LOG[:2])):
            session.add_all(RunEvent(conversation_id=conversation.id, seq=e.seq, workspace_id=ws, host_epoch="e",
                                     host_cursor=e.seq, kind=e.kind, data=e.data, ui=[],
                                     created_at=NOW - timedelta(minutes=30) + timedelta(seconds=e.seq))
                            for e in events)
        session.add(AnswerFeedback(conversation_id=mine.id, run_id="run-1", workspace_id=ws, user_id="alice",
                                   rating="dislike"))
        session.add_all(UsageRecord(workspace_id=ws, user_id="alice", conversation_id=mine.id, turn=u.turn,
                                    step=u.step, model_alias=u.model_alias, input_tokens=u.input_tokens,
                                    output_tokens=u.output_tokens, total_tokens=u.input_tokens + u.output_tokens,
                                    cost=u.cost, duration_ms=u.duration_ms, status=u.status, occurred_at=NOW,
                                    day=NOW.date()) for u in USAGE)
        await session.commit()
    await summarize(sessionmaker, ws, {mine.id: ("alice", LOG), own.id: ("boss", LOG[:2])})
    sink = Sink()
    debug = build_debugview(sessionmaker, UsageRecorder(sessionmaker))
    records = build_run_records(sessionmaker, UsageRecorder(sessionmaker), debug, Finder([mine, own]), access, sink,
                                profiles)

    async def principal(x_user: str = Header()) -> Principal:
        return Principal(user_id=x_user, auth_method="session")

    app = FastAPI()
    app.include_router(build_admin_router(records, principal))
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://kw")
    yield {"ws": ws, "mine": mine, "own": own, "sink": sink, "http": http}
    await http.aclose()


async def test_admin_lists_every_turn_with_status_usage_and_member_name(env):
    http, ws, mine = env["http"], env["ws"], env["mine"]
    body = (await http.get(f"/api/workspaces/{ws}/runs", headers={"x-user": "boss"})).json()
    rows = {(r["conversationId"], r["turn"]): r for r in body["items"]}
    first = rows[(mine.id, 1)]
    assert (first["title"], first["userName"], first["status"], first["modelCalls"], first["cost"]) == (
        "请假条", "alice 的名字", "completed", 2, "0.0012")
    assert first["durationMs"] is not None
    assert (first["searches"], first["cited"], first["rating"], first["tools"]) == (1, 0, "dislike", ["bash"])
    assert (rows[(mine.id, 2)]["status"], rows[(mine.id, 2)]["question"]) == ("running", "再来一次")
    assert rows[(env["own"].id, 1)]["status"] == "running"
    running = (await http.get(f"/api/workspaces/{ws}/runs", params={"status": "completed"},
                              headers={"x-user": "boss"})).json()
    assert [(r["conversationId"], r["turn"]) for r in running["items"]] == [(mine.id, 1)]
    mine_only = (await http.get(f"/api/workspaces/{ws}/runs", params={"user": "boss"},
                                headers={"x-user": "boss"})).json()
    assert {r["userId"] for r in mine_only["items"]} == {"boss"}


async def test_runs_can_be_narrowed_to_uncited_disliked_or_unanswered_turns(env):
    """检索了知识库却没引用（答完的才算）、被点了踩、判断没答上来的轮次，单独筛出来。"""
    http, ws, mine = env["http"], env["ws"], env["mine"]
    for signal in ("uncited", "disliked", "unanswered"):
        body = (await http.get(f"/api/workspaces/{ws}/runs", params={"signal": signal},
                               headers={"x-user": "boss"})).json()
        assert [(r["conversationId"], r["turn"]) for r in body["items"]] == [(mine.id, 1)], signal
    bad = await http.get(f"/api/workspaces/{ws}/runs", params={"signal": "slow"}, headers={"x-user": "boss"})
    assert bad.status_code == 422


async def test_runs_can_be_narrowed_to_a_tool_or_a_knowledge_base(env):
    """问答大盘点工具、知识库那一行，跳到运行记录里用过这个工具、检索到这个库的那些轮。"""
    http, ws, mine = env["http"], env["ws"], env["mine"]
    for params, expected in (({"tool": "bash"}, [(mine.id, 1)]), ({"tool": "never"}, []),
                             ({"base": "builtin.kb1"}, [(mine.id, 1)]), ({"base": "builtin.kb9"}, [])):
        body = (await http.get(f"/api/workspaces/{ws}/runs", params=params, headers={"x-user": "boss"})).json()
        assert [(r["conversationId"], r["turn"]) for r in body["items"]] == expected, params


async def test_viewing_someone_elses_run_is_audited_and_members_are_refused(env):
    http, ws, mine, sink = env["http"], env["ws"], env["mine"], env["sink"]
    base = f"/api/workspaces/{ws}/runs/{mine.id}/turns/1"
    assert (await http.get(base, headers={"x-user": "alice"})).status_code == 403, "会话本人在对话里看，不走管理端"
    assert (await http.get(f"/api/workspaces/{ws}/runs", headers={"x-user": "alice"})).status_code == 403
    detail = (await http.get(base, headers={"x-user": "boss"})).json()
    assert detail["summary"]["request"] == "你好"
    assert (await http.get(f"{base}/steps/2/input", headers={"x-user": "boss"})).status_code == 200
    exported = await http.get(f"{base}/export", headers={"x-user": "boss"})
    assert exported.headers["content-disposition"].startswith("attachment")
    assert [(e.action, e.actor_id, e.resource_id, e.detail) for e in sink.events] == [
        ("run.view", "boss", mine.id, {"turn": 1, "owner": "alice"}),
        ("run.view_input", "boss", mine.id, {"turn": 1, "step": 2, "owner": "alice"}),
        ("run.export", "boss", mine.id, {"turn": 1, "owner": "alice"})]
    await http.get(f"/api/workspaces/{ws}/runs/{env['own'].id}/turns/1", headers={"x-user": "boss"})
    assert len(sink.events) == 3, "看自己的不记"
    assert (await http.get(f"/api/workspaces/{ws}/runs/c_missing/turns/1", headers={"x-user": "boss"})).status_code == 404


async def test_time_range_is_bounded(env):
    http, ws = env["http"], env["ws"]
    response = await http.get(f"/api/workspaces/{ws}/runs", headers={"x-user": "boss"},
                              params={"since": "2026-01-01T00:00:00Z", "until": "2026-10-01T00:00:00Z"})
    assert response.status_code == 422
