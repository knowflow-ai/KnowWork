"""问答大盘：每轮汇总的聚合，只给空间所有者和管理员。四个数各和上一段比、按天、需要关注（结束原因、点踩分类、
检索了没引用）、工具与知识库两个维度；按统计时区分天。"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest
from fastapi import FastAPI, Header

from knowwork_server.access.service import AccessService
from knowwork_server.auth.principal import Principal
from knowwork_server.conversations.models import AnswerFeedback, Conversation, RunSummary
from knowwork_server.insights import build_insights, build_router
from knowwork_server.models.tables import UsageRecord

pytestmark = pytest.mark.anyio
TODAY = date(2026, 10, 26)
ZONE = "Asia/Shanghai"


def at(day: int, hour: int = 4) -> datetime:
    """10 月 day 日 hour 点（UTC），北京时间晚 8 小时。"""
    return datetime(2026, 10, day, hour, tzinfo=UTC)


def knowledge(bases, cited_bases, cited) -> dict:
    return {"knowledge": {"searches": 1, "cited": cited, "bases": bases, "cited_bases": cited_bases}}


@pytest.fixture
async def env(sessionmaker):
    ws = "ws_" + uuid.uuid4().hex[:10]
    access = AccessService(sessionmaker)
    assert await access.reload()
    await access.create_workspace(workspace_id=ws, name="大盘", owner_user_id="boss")
    await access.add_member("boss", ws, "lead", "admin")
    await access.add_member("boss", ws, "alice", "editor")
    turns = [  # (会话, 轮, 提问人, 开始, 状态, 结束原因, 工具, 事实, 赞踩, 点踩分类)
        ("c1", 1, "alice", at(25), "completed", "completed", ["knowledge_search"],
         knowledge(["builtin.kb1"], ["builtin.kb1"], 2), "like", None),
        ("c1", 2, "alice", at(25, 5), "completed", "completed", ["knowledge_search", "bash"],
         knowledge(["builtin.kb1", "kf.ds9"], [], 0), "dislike", "task-result"),
        ("c2", 1, "bob", at(26), "failed", "error", ["bash"], {}, "dislike", None),
        ("c2", 2, "bob", at(26, 1), "cancelled", "aborted", [], {}, None, None),
        ("c2", 3, "bob", at(26, 2), "running", None, [], {}, None, None),
        ("c3", 1, "alice", at(25, 17), "failed", "blocked", ["bash"], {}, None, None),  # 北京时间 26 日凌晨
        ("c4", 1, "carol", at(15), "completed", "completed", [], {}, None, None),  # 上一段
    ]
    async with sessionmaker() as session:
        for cid, user in {(t[0], t[2]) for t in turns}:
            session.add(Conversation(id=f"{ws}-{cid}", workspace_id=ws, user_id=user, title=cid))
        await session.flush()
        for cid, turn, user, started, status, reason, tools, facts, rating, category in turns:
            session.add(RunSummary(conversation_id=f"{ws}-{cid}", turn=turn, workspace_id=ws, user_id=user,
                                   question="问", status=status, end_reason=reason, started_at=started,
                                   ended_at=None if status == "running" else started, tools=tools, facts=facts))
            if rating:
                session.add(AnswerFeedback(conversation_id=f"{ws}-{cid}", run_id=f"run-{turn}", workspace_id=ws,
                                           user_id=user, rating=rating, category=category))
        session.add(UsageRecord(workspace_id=ws, user_id="alice", conversation_id=f"{ws}-c1", turn=1, step=1,
                                model_alias="default", input_tokens=1000, output_tokens=200, total_tokens=1200,
                                cost=Decimal("0.0350"), duration_ms=10, status="ok", occurred_at=at(25),
                                day=date(2026, 10, 25)))
        await session.commit()

    async def names(workspace_id, user_id):
        return {"builtin.kb1": "制度库"}

    insights = build_insights(sessionmaker, access, timezone=ZONE, base_names=names)

    async def principal(x_user: str = Header()) -> Principal:
        return Principal(user_id=x_user, auth_method="session")

    app = FastAPI()
    app.include_router(build_router(insights, principal))
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://kw")
    yield {"ws": ws, "insights": insights, "http": http}
    await http.aclose()


async def test_the_overview_counts_turns_problems_tools_and_knowledge_bases(env):
    found = await env["insights"].overview("boss", env["ws"], 7, today=TODAY)
    assert (found.start, found.end) == (date(2026, 10, 20), TODAY)
    assert (found.since.isoformat(), found.until.isoformat()) == ("2026-10-20T00:00:00+08:00", "2026-10-27T00:00:00+08:00")
    t = found.totals
    assert (t.turns, t.askers, t.completed, t.failed, t.cancelled, t.liked, t.disliked, t.searched) == (
        6, 2, 2, 2, 1, 1, 2, 2)
    assert (t.cost, t.input_tokens, t.output_tokens) == ("0.035", 1000, 200)
    assert (found.previous.turns, found.previous.cost) == (1, "0")
    assert [(d.date, d.turns, d.completed, d.ended) for d in found.days][-2:] == [
        ("2026-10-25", 2, 2, 2), ("2026-10-26", 4, 0, 3)], "北京时间分天：c3 那轮算 26 日"
    assert [(c.status, c.key, c.count) for c in found.end_reasons] == [
        ("cancelled", "aborted", 1), ("failed", "blocked", 1), ("failed", "error", 1)]
    assert [(c.key, c.count) for c in found.dislikes] == [("", 1), ("task-result", 1)]
    assert found.uncited == 1
    assert [(u.name, u.turns, u.failed) for u in found.tools] == [("bash", 3, 2), ("knowledge_search", 2, 0)]
    assert [(b.id, b.name, b.turns, b.cited, b.unused, b.disliked) for b in found.bases] == [
        ("builtin.kb1", "制度库", 2, 1, 1, 1), ("kf.ds9", "kf.ds9", 1, 0, 1, 1)], "名字取不到的显示全局 id"


async def test_only_owners_and_admins_see_it(env):
    http, ws = env["http"], env["ws"]
    for user in ("boss", "lead"):
        response = await http.get(f"/api/workspaces/{ws}/insights", params={"days": 30}, headers={"x-user": user})
        assert response.status_code == 200, user
    body = response.json()
    assert len(body["days"]) == 30 and body["totals"]["turns"] == 7 and body["bases"][0]["name"] == "制度库"
    denied = await http.get(f"/api/workspaces/{ws}/insights", headers={"x-user": "alice"})
    assert (denied.status_code, denied.json()["detail"]) == (403, "只有空间所有者和管理员能看问答大盘")
    bad = await http.get(f"/api/workspaces/{ws}/insights", params={"days": 14}, headers={"x-user": "boss"})
    assert bad.status_code == 422
