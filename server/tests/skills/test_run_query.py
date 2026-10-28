"""管理端「技能 → 最近运行」：按版本汇总与最近运行的查询、翻页、筛选，只有空间管理员能看。"""

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request

from knowwork_server.access.service import AccessService
from knowwork_server.auth.principal import Principal
from knowwork_server.conversations.models import Conversation
from knowwork_server.skills import build_runs_router
from knowwork_server.skills.models import Skill
from knowwork_server.skills.run_query import SkillRunQuery
from knowwork_server.skills.runs import RunFinish, RunStart, SkillRunStore, StepRecord, TurnRun

pytestmark = pytest.mark.anyio
NOW = datetime.now(UTC).replace(microsecond=0)


async def principal(request: Request) -> Principal:
    user = request.headers.get("x-user")
    if not user:
        raise HTTPException(status_code=401)
    scopes = request.headers.get("x-scopes")
    if scopes is None:
        return Principal(user_id=user, auth_method="session")
    return Principal(user_id=user, auth_method="api_key", scopes=frozenset(filter(None, scopes.split(","))))


def skill_row(ws, name, scope="workspace"):
    return Skill(id=uuid.uuid4().hex, workspace_id=ws, scope=scope,
                 scope_key="workspace" if scope == "workspace" else "personal:alice", owner_id="alice", name=name,
                 description=name, status="published", draft_digest="d" * 64, draft_manifest={})


def run(ws, skill_id, run_id, *, version, status="succeeded", stage=None, ago=0, seconds=4, mode="manual",
        steps=("extract_records",), failed=0, cost=0.01):
    started = NOW - timedelta(minutes=ago)
    return TurnRun(
        start=RunStart(workspace_id=ws, run_id=run_id, session_id="root", user_id="carol",
                       skill_id=skill_id, skill_version=version, selection_mode=mode),
        started_at=started, ended_at=started + timedelta(seconds=seconds),
        finish=RunFinish(status=status, total_tokens=100 * seconds, cost=cost, artifact_count=1,
                         failure_stage=stage, failure_code="completion_missing" if stage else None),
        steps=tuple(StepRecord(step_key=name, step_type="tool", seq=i, tool_name=name,
                               status="failed" if i < failed else "succeeded") for i, name in enumerate(steps)))


@pytest.fixture
async def env(sessionmaker):
    ws = "ws_" + uuid.uuid4().hex[:10]
    access = AccessService(sessionmaker)
    assert await access.reload()
    await access.create_workspace(workspace_id=ws, name="最近运行", owner_user_id="alice")
    await access.add_member("alice", ws, "bob", "admin")
    await access.add_member("alice", ws, "carol", "editor")
    report, personal = skill_row(ws, "report"), skill_row(ws, "notes", "personal")
    chat = Conversation(id="c_" + uuid.uuid4().hex, workspace_id=ws, user_id="carol", title="周报")
    async with sessionmaker() as session:
        session.add_all([report, personal, chat])
        await session.commit()
    store = SkillRunStore(sessionmaker)
    turns = [
        run(ws, report.id, f"{chat.id}:1", version="1.0.0", ago=60 * 24 * 40),  # 超出默认的 30 天
        run(ws, report.id, f"{chat.id}:2", version="1.0.0", status="failed", stage="tool", ago=50, failed=1,
            steps=("extract_records", "docx_render")),
        run(ws, report.id, f"{chat.id}:3", version="1.1.0", ago=30, seconds=2),
        run(ws, report.id, f"{chat.id}:4", version="1.1.0", status="failed", stage="knowledge", ago=20),
        run(ws, report.id, f"{chat.id}:5", version="1.1.0", status="failed", stage="tool", ago=15),
        run(ws, report.id, f"{chat.id}:6", version="1.1.0", status="cancelled", ago=10),
        run(ws, report.id, "workflow-run-7", version="1.1.0", ago=5, mode="workflow"),  # 固定工作流的运行
    ]
    for item in turns:
        await store.replace_turn(ws, item.start.run_id, [item])
    app = FastAPI()
    app.include_router(build_runs_router(SkillRunQuery(sessionmaker), access, principal))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as http:
        yield http, ws, report.id, personal.id, chat.id


async def test_recent_runs_newest_first_with_paging(env):
    http, ws, skill_id, _, chat = env
    base = f"/api/workspaces/{ws}/skills/{skill_id}/runs"
    first = await http.get(base, params={"limit": 2}, headers={"x-user": "bob"})
    assert first.status_code == 200, first.text
    body = first.json()
    workflow, cancelled = body["items"]
    # 拆不出「会话:轮」的运行（例如固定工作流）不给会话
    assert (workflow["selectionMode"], workflow["conversationId"], workflow["turn"]) == ("workflow", None, None)
    assert (cancelled["status"], cancelled["conversationId"], cancelled["turn"], cancelled["title"]) == (
        "cancelled", chat, 6, "周报")
    assert (cancelled["durationMs"], cancelled["totalTokens"], cancelled["cost"], cancelled["steps"]) == (
        4000, 400, "0.01", 1)
    rest = await http.get(base, params={"limit": 2, "before": body["next"]}, headers={"x-user": "bob"})
    tail = await http.get(base, params={"limit": 2, "before": rest.json()["next"]}, headers={"x-user": "bob"})
    turns = [item["turn"] for item in rest.json()["items"] + tail.json()["items"]]
    assert turns == [5, 4, 3, 2] and tail.json()["next"] is None  # 第 1 轮在 30 天以外
    older = await http.get(base, params={"since": (NOW - timedelta(days=60)).isoformat()}, headers={"x-user": "bob"})
    assert len(older.json()["items"]) == 7
    failed = await http.get(base, params={"status": "failed", "version": "1.0.0"}, headers={"x-user": "bob"})
    (only,) = failed.json()["items"]
    assert (only["turn"], only["failureStage"], only["steps"], only["failedSteps"]) == (2, "tool", 2, 1)


async def test_summary_by_version(env):
    http, ws, skill_id, _, _ = env
    response = await http.get(f"/api/workspaces/{ws}/skills/{skill_id}/runs/summary", headers={"x-user": "bob"})
    assert response.status_code == 200, response.text
    latest, older = response.json()["versions"]
    assert (latest["version"], latest["runs"], latest["succeeded"], latest["failed"], latest["cancelled"]) == (
        "1.1.0", 5, 2, 2, 1)
    # 失败阶段工具、知识各一次：次数相同取阶段名靠前的，结果稳定
    assert latest["topFailureStage"] == "knowledge"
    assert (older["version"], older["runs"], older["failed"], older["topFailureStage"]) == ("1.0.0", 1, 1, "tool")
    assert (older["avgDurationMs"], older["avgTokens"]) == (4000, 400)


async def test_only_workspace_admins_see_runs(env):
    http, ws, skill_id, personal_id, _ = env
    base = f"/api/workspaces/{ws}/skills"
    assert (await http.get(f"{base}/{skill_id}/runs", headers={"x-user": "carol"})).status_code == 403
    assert (await http.get(f"{base}/{skill_id}/runs/summary", headers={"x-user": "carol"})).status_code == 403
    no_scope = await http.get(f"{base}/{skill_id}/runs", headers={"x-user": "bob", "x-scopes": "models:read"})
    assert no_scope.status_code == 403
    scoped = await http.get(f"{base}/{skill_id}/runs", headers={"x-user": "bob", "x-scopes": "skills:read"})
    assert scoped.status_code == 200
    # 个人技能、别的技能、改坏的翻页游标
    assert (await http.get(f"{base}/{personal_id}/runs", headers={"x-user": "bob"})).status_code == 404
    assert (await http.get(f"{base}/missing/runs/summary", headers={"x-user": "bob"})).status_code == 404
    broken = await http.get(f"{base}/{skill_id}/runs", params={"before": "昨天"}, headers={"x-user": "bob"})
    assert broken.status_code == 400
