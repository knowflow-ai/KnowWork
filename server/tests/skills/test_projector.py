"""技能运行事实的投影器：回合结束后在后台从库里的会话日志投影，等帧落库、等产物收完，可重算。"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from knowwork_server.access.service import AccessService
from knowwork_server.conversations.listeners import StoredEvent
from knowwork_server.conversations.models import Conversation, RunEvent
from knowwork_server.conversations.runlog import RunLog
from knowwork_server.models.recorder import UsageRecorder
from knowwork_server.models.tables import UsageRecord
from knowwork_server.skills.models import Skill, SkillRun, SkillStepRun
from knowwork_server.skills.projector import LoggedTurns, SkillRunProjector, run_id_of
from knowwork_server.skills.runs import SkillRunStore

pytestmark = pytest.mark.anyio
NOW = datetime.now(UTC)


def session_event(kind, data):
    return "session/event", {"type": kind, "time": int(NOW.timestamp() * 1000), "data": data}


def tool(call_id, name, error=False):
    return [session_event("tool/call", {"turn": 1, "step": 1, "callId": call_id, "name": name, "arguments": "{}"}),
            session_event("tool/result", {"turn": 1, "step": 1, "message": {
                "role": "tool", "toolCallId": call_id, "isError": error, "content": []}})]


LOADED = ("skill/loaded", {"sessionId": "root", "skill": "report", "version": "1.2.0", "mode": "manual",
                           "unlocked": []})
TURN_ONE = [session_event("turn/start", {"turn": 1}), LOADED, session_event("step/start", {"turn": 1, "step": 1}),
            *tool("c1", "extract_records"), *tool("c2", "docx_render", error=True),
            session_event("deliverables/presented", {"files": [{"path": "out/报告.docx"}]}),
            session_event("turn/end", {"turn": 1, "reason": {"kind": "completed"}})]
TURN_TWO = [session_event("turn/start", {"turn": 2}), session_event("turn/end", {"turn": 2, "reason": {"kind": "completed"}})]


@dataclass(frozen=True)
class Item:
    run_id: str | None


class Artifacts:
    def __init__(self, items):
        self.items = items

    async def list(self, conversation):
        return self.items


@dataclass
class Env:
    ws: str
    conversation: str
    skill_id: str
    store: SkillRunStore
    turns: LoggedTurns


@pytest.fixture
async def env(sessionmaker):
    ws = "ws_" + uuid.uuid4().hex[:10]
    access = AccessService(sessionmaker)
    assert await access.reload()
    await access.create_workspace(workspace_id=ws, name="投影", owner_user_id="alice")
    conversation = Conversation(id="c_" + uuid.uuid4().hex, workspace_id=ws, user_id="alice", title="周报")
    skill = Skill(id=uuid.uuid4().hex, workspace_id=ws, scope="workspace", scope_key="workspace", owner_id="alice",
                  name="report", description="写周报", status="published", draft_digest="d" * 64, draft_manifest={})
    async with sessionmaker() as session:
        session.add_all([conversation, skill])
        await session.flush()
        session.add_all(RunEvent(conversation_id=conversation.id, seq=seq, workspace_id=ws, host_epoch="e",
                                 host_cursor=seq, kind=kind, data=data, ui=[], created_at=NOW)
                        for seq, (kind, data) in enumerate([*TURN_ONE, *TURN_TWO]))
        session.add(UsageRecord(workspace_id=ws, user_id="alice", conversation_id=conversation.id, turn=1, step=1,
                                model_alias="default", input_tokens=300, output_tokens=40, total_tokens=340,
                                cost=Decimal("0.0042"), duration_ms=800, status="ok", occurred_at=NOW, day=NOW.date()))
        await session.commit()
    artifacts = Artifacts([Item("run-1"), Item("run-2"), Item(None)])
    return Env(ws=ws, conversation=conversation.id, skill_id=skill.id, store=SkillRunStore(sessionmaker),
               turns=LoggedTurns(sessionmaker, UsageRecorder(sessionmaker), artifacts))


async def stored(sessionmaker, ws):
    """测试库是共用的，只看本测试的空间。"""
    async with sessionmaker() as session:
        runs = (await session.scalars(select(SkillRun).where(SkillRun.workspace_id == ws))).all()
        steps = (await session.scalars(select(SkillStepRun).where(SkillStepRun.workspace_id == ws)
                                       .order_by(SkillStepRun.seq))).all()
    return runs, steps


async def nothing(conversation_id):
    return None


async def test_project_reads_one_turn_and_is_repeatable(env, sessionmaker):
    projector = SkillRunProjector(env.turns, env.store, lambda cid: [], nothing)
    assert await projector.project(env.ws, env.conversation, 1) == 1
    assert await projector.project(env.ws, env.conversation, 1) == 1  # 重算不重复
    assert await projector.project(env.ws, env.conversation, 2) == 0  # 这一轮没加载技能
    assert await projector.project(env.ws, "c_missing", 1) == 0
    (run,), steps = await stored(sessionmaker, env.ws)
    assert (run.run_id, run.session_id, run.user_id, run.skill_id, run.skill_version, run.selection_mode) == (
        run_id_of(env.conversation, 1), "root", "alice", env.skill_id, "1.2.0", "manual")
    # 交付过、也收下了一个产物；中途有工具出错但回合正常完成，运行算成功
    assert (run.status, run.total_tokens, run.cost, run.artifact_count, run.failure_stage) == (
        "succeeded", 340, Decimal("0.004200"), 1, None)
    assert [(s.seq, s.tool_name, s.status) for s in steps] == [(0, "extract_records", "succeeded"),
                                                                (1, "docx_render", "failed")]


async def test_turn_events_take_the_last_turn_with_that_number(env, sessionmaker):
    log = RunLog(sessionmaker)
    first = await log.turn_events(env.conversation, 1)
    assert [e.seq for e in first.events] == list(range(len(TURN_ONE)))
    second = await log.turn_events(env.conversation, 2)
    assert [e.seq for e in second.events] == [len(TURN_ONE), len(TURN_ONE) + 1]
    assert (await log.turn_events(env.conversation, 7)).events == ()


async def test_observer_waits_for_frames_and_artifacts_then_projects(env, sessionmaker):
    waits = {"pending": 3}
    settled = []

    def pending(conversation_id):
        # 前几次查询还有没提交的帧（包括这一轮的 turn/end），之后落库完成
        waits["pending"] -= 1
        return [StoredEvent(conversation_id=conversation_id, seq=9, kind="session/event", data={}, ui=())] \
            if waits["pending"] > 0 else []

    async def settle(conversation_id):
        settled.append(conversation_id)

    projector = SkillRunProjector(env.turns, env.store, pending, settle)
    end = {"type": "turn/end", "data": {"turn": 1, "reason": {"kind": "completed"}}}
    projector(env.conversation, env.ws, "session/event", {"type": "turn/start", "data": {"turn": 1}})
    projector(env.conversation, env.ws, *LOADED)
    projector(env.conversation, env.ws, "session/event", end)
    await projector.drain()
    assert settled == [env.conversation] and waits["pending"] <= 0
    runs, _ = await stored(sessionmaker, env.ws)
    assert [r.skill_id for r in runs] == [env.skill_id]


async def test_turns_without_skills_cost_nothing():
    class Turns:
        calls = 0

        async def read(self, *args):
            Turns.calls += 1

    projector = SkillRunProjector(Turns(), None, lambda cid: [], nothing)
    projector("c1", "ws", "session/event", {"type": "turn/end", "data": {"turn": 1}})
    projector("c1", "ws", "session/event", {"type": "tool/call", "data": {}})
    projector("c1", "ws", "skill/loaded", {"skill": "x"})
    projector("c1", "ws", "session/event", {"type": "turn/end", "data": {"turn": "?"}})  # 轮号不对，丢掉
    await projector.drain()
    projector("c1", "ws", "session/event", {"type": "turn/end", "data": {"turn": 2}})  # 上一轮已经结算过
    await projector.drain()
    assert Turns.calls == 0
