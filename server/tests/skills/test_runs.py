"""技能运行事实的写入接口。"""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from knowwork_server.access.rls import set_workspace
from knowwork_server.skills.errors import SkillConflict, SkillNotFound
from knowwork_server.skills.models import SkillRun, SkillStepRun
from knowwork_server.skills.runs import RunFinish, RunStart, StepRecord, TurnRun

from .conftest import actor, skill_zip

pytestmark = pytest.mark.anyio

START = RunStart(workspace_id="ws1", run_id="run-1", session_id="sess-1", user_id="dave", skill_id="sk1",
                 skill_version="1.0.0", selection_mode="automatic")


async def test_run_lifecycle(env):
    runs = env.skills.runs
    run_id = await runs.start(START)
    await runs.record_step("ws1", run_id, StepRecord(step_key="extract", step_type="tool", seq=1, status="succeeded",
                                                     duration_ms=120, tool_name="extract_records",
                                                     artifact_refs=("art-1",)))
    with pytest.raises(SkillConflict):
        await runs.record_step("ws1", run_id, StepRecord(step_key="again", step_type="tool", seq=1, status="failed"))
    await runs.finish("ws1", run_id, RunFinish(status="failed", total_tokens=900, cost=0.12, artifact_count=1,
                                               failure_stage="tool", failure_code="tool_failed"))
    with pytest.raises(SkillConflict):
        await runs.finish("ws1", run_id, RunFinish(status="succeeded"))
    async with env.sessionmaker() as session, session.begin():
        await set_workspace(session, "ws1")
        run = await session.get(SkillRun, run_id)
        steps = (await session.scalars(select(SkillStepRun))).all()
    assert (run.status, run.failure_stage, run.total_tokens, run.ended_at is not None) == ("failed", "tool", 900, True)
    assert [(s.tool_name, s.artifact_refs) for s in steps] == [("extract_records", ["art-1"])]


async def test_run_validation(env):
    runs = env.skills.runs
    with pytest.raises(ValidationError):
        RunStart(**{**START.model_dump(), "selection_mode": "guess"})
    with pytest.raises(ValidationError):
        RunFinish(status="failed", failure_stage="unknown")
    with pytest.raises(ValueError):
        await runs.finish("ws1", "x", RunFinish(status="failed"))
    with pytest.raises(SkillNotFound):
        await runs.finish("ws1", "missing", RunFinish(status="succeeded"))
    with pytest.raises(SkillNotFound):
        await runs.record_step("ws1", "missing", StepRecord(step_key="s", step_type="model", seq=0, status="skipped"))
    run_id = await runs.start(START)
    with pytest.raises(SkillNotFound):  # 别的空间看不到这条运行
        await runs.record_step("ws2", run_id, StepRecord(step_key="s", step_type="model", seq=0, status="skipped"))


def turn_run(skill_id="sk1", status="succeeded", *, run_id="c1:1", workspace="ws1", steps=2, stage=None):
    at = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)
    return TurnRun(start=RunStart(**{**START.model_dump(), "run_id": run_id, "skill_id": skill_id,
                                     "workspace_id": workspace}),
                   started_at=at, ended_at=at + timedelta(seconds=3),
                   finish=RunFinish(status=status, total_tokens=50, cost=0.01, failure_stage=stage),
                   steps=tuple(StepRecord(step_key=f"t{i}", step_type="tool", seq=i, status="succeeded",
                                          tool_name=f"t{i}") for i in range(steps)))


async def stored(env, workspace="ws1"):
    async with env.sessionmaker() as session, session.begin():
        await set_workspace(session, workspace)
        runs = (await session.scalars(select(SkillRun).order_by(SkillRun.skill_id))).all()
        steps = (await session.scalars(select(SkillStepRun))).all()
    return runs, steps


async def test_replace_turn_is_idempotent(env):
    runs = env.skills.runs
    await runs.replace_turn("ws1", "c1:1", [turn_run("sk1"), turn_run("sk2", "failed", stage="tool", steps=1)])
    await runs.replace_turn("ws1", "c1:2", [turn_run("sk1", run_id="c1:2")])
    await runs.replace_turn("ws1", "c1:1", [turn_run("sk1"), turn_run("sk2", "failed", stage="tool", steps=1)])
    rows, steps = await stored(env)
    assert sorted((r.run_id, r.skill_id, r.status) for r in rows) == [
        ("c1:1", "sk1", "succeeded"), ("c1:1", "sk2", "failed"), ("c1:2", "sk1", "succeeded")]
    assert len(steps) == 2 + 1 + 2
    assert {(r.started_at.second, r.ended_at.second) for r in rows} == {(0, 3)}
    await runs.replace_turn("ws1", "c1:1", [])  # 重算发现这一轮没有技能：清掉，步骤跟着删
    rows, steps = await stored(env)
    assert [(r.run_id, len(steps)) for r in rows] == [("c1:2", 2)]


async def test_replace_turn_validation(env):
    runs = env.skills.runs
    with pytest.raises(ValueError):
        await runs.replace_turn("ws1", "c1:1", [turn_run(run_id="c1:9")])
    with pytest.raises(ValueError):
        await runs.replace_turn("ws1", "c1:1", [turn_run(workspace="ws2")])
    with pytest.raises(ValueError):
        await runs.replace_turn("ws1", "c1:1", [turn_run(status="failed")])


async def test_skill_ids_prefer_workspace_skills(env):
    svc = env.skills.service

    async def published(user, scope, name):
        view = await svc.install(actor(user), scope, skill_zip(name))
        await svc.publish(actor(user), view.id)
        return view.id

    shared = await published("bob", "workspace", "summary")
    await published("dave", "personal", "summary")
    mine = await published("dave", "personal", "my-notes")
    await published("carol", "personal", "carol-notes")
    ids = await env.skills.runs.skill_ids("ws1", "dave", {"summary", "my-notes", "carol-notes", "gone"})
    assert ids == {"summary": shared, "my-notes": mine}
    assert await env.skills.runs.skill_ids("ws1", "dave", set()) == {}
