"""workflows 测试夹具：独立数据库（access、skills、tools、workflows 的表），access / skills / workflows 开行级安全，
业务读写用普通角色；假内核按脚本回答；假工具记录调用次数。"""

import asyncio
import io
import json
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from knowwork_server.access.models import ACCESS_RLS_TABLES, ACCESS_TABLES
from knowwork_server.access.rls import enable_row_level_security
from knowwork_server.access.service import AccessService
from knowwork_server.db.base import Base, make_engine, make_sessionmaker
from knowwork_server.kernel.contract import CancelResult, KernelEvent, SessionNotFound
from knowwork_server.skills import SKILL_RLS_TABLES, SKILL_TABLES, LocalBlobStore, build_skills
from knowwork_server.skills.views import Actor as SkillActor
from knowwork_server.tools import build_tool_service
from knowwork_server.tools.models import ActionReceipt, ActionResultBlob
from knowwork_server.tools.registry import BuiltinTool, StaticToolRegistry, ToolFailure, ToolSpec
from knowwork_server.workflows import WORKFLOW_RLS_TABLES, WORKFLOW_TABLES, build_workflows
from knowwork_server.workflows.service import Actor

APP_ROLE = "kw_workflows_app"
MASTER = "workflows-test-master-key-".ljust(40, "k")
TOOL_TABLES = (ActionReceipt.__table__, ActionResultBlob.__table__)


def _swap(url: str, *, user: str, database: str) -> str:
    return url.replace("postgres@", f"{user}@", 1).replace("/postgres?", f"/{database}?", 1)


@pytest.fixture
async def workflows_db(database_url):
    name = "kw_wf_" + uuid.uuid4().hex[:12]
    server = create_async_engine(database_url, isolation_level="AUTOCOMMIT")
    async with server.connect() as conn:
        if not await conn.scalar(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": APP_ROLE}):
            await conn.execute(text(f"CREATE ROLE {APP_ROLE} LOGIN NOSUPERUSER NOBYPASSRLS"))
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    admin = create_async_engine(_swap(database_url, user="postgres", database=name))
    async with admin.begin() as conn:
        tables = [*ACCESS_TABLES, *SKILL_TABLES, *TOOL_TABLES, *WORKFLOW_TABLES]
        await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
        await enable_row_level_security(conn, (*ACCESS_RLS_TABLES, *SKILL_RLS_TABLES, *WORKFLOW_RLS_TABLES))
        await conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}"))
        await conn.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}"))
        await conn.execute(text(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}"))
    await admin.dispose()
    yield _swap(database_url, user=APP_ROLE, database=name)
    async with server.connect() as conn:
        await conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    await server.dispose()


# ---- 假工具 ----

@dataclass
class ToolBox:
    calls: dict[str, list[dict]] = field(default_factory=dict)
    gates: dict[str, asyncio.Event] = field(default_factory=dict)  # 设置后该工具执行时卡住，直到事件被置位

    def record(self, name: str, arguments: dict) -> None:
        self.calls.setdefault(name, []).append(arguments)

    def count(self, name: str) -> int:
        return len(self.calls.get(name, []))

    def registry(self) -> StaticToolRegistry:
        def tool(name: str, risk: str, handler) -> BuiltinTool:
            schema = {"type": "object", "additionalProperties": True}
            return BuiltinTool(spec=ToolSpec(name=name, description=name, input_schema=schema, risk=risk),
                               handler=handler)

        async def extract(_ctx, arguments):
            self.record("kw_extract", arguments)
            return json.dumps({"rows": [{"party": f"甲方-{arguments.get('id')}"}], "id": arguments.get("id")},
                              ensure_ascii=False)

        async def write(_ctx, arguments):
            self.record("kw_write", arguments)
            gate = self.gates.get("kw_write")
            if gate is not None:
                await gate.wait()
            return json.dumps({"saved": True, "text": arguments.get("text")}, ensure_ascii=False)

        async def fail(_ctx, arguments):
            self.record("kw_fail", arguments)
            raise ToolFailure("上游系统拒绝了这次请求")

        async def search(_ctx, arguments):
            self.record("kw_search", arguments)
            return "[]"

        return StaticToolRegistry.of([tool("kw_extract", "read", extract), tool("kw_write", "write", write),
                                      tool("kw_fail", "read", fail), tool("kw_search", "read", search)])


# ---- 假内核：每次 send 由 responder 给出助手回答 ----

def _frames(turn: int, reply: str | None, kind: str = "stop") -> list[tuple[str, dict]]:
    events = [("session/event", {"type": "turn/start", "data": {"turn": turn}})]
    if reply is not None:
        events.append(("session/event", {"type": "assistant/message", "data": {
            "turn": turn, "message": {"id": f"m{turn}", "content": [{"type": "text", "text": reply}]}}}))
    events.append(("session/event", {"type": "turn/end", "data": {"turn": turn, "reason": {"kind": kind}}}))
    return events


APPROVAL = object()  # responder 返回它：本轮请求一次工具审批，然后挂起
LOSE = object()  # responder 返回它：宿主丢了这个会话


class ScriptedKernel:
    def __init__(self, responder: Callable[[str, str, int], object] | None = None) -> None:
        self.responder = responder or (lambda _sid, _text, _n: '{"summary": "ok"}')
        self.opened: list = []
        self.sent: list[tuple[str, str]] = []
        self.disposed: list[str] = []
        self.cancelled: list[str] = []
        self._frames: dict[str, list[KernelEvent]] = {}
        self._changed: dict[str, asyncio.Event] = {}

    async def open_session(self, spec):
        self.opened.append(spec)
        self._frames.setdefault(spec.session_id, [])
        self._changed.setdefault(spec.session_id, asyncio.Event())
        return "e1"

    def _push(self, session_id: str, kind: str, data: dict) -> None:
        frames = self._frames[session_id]
        frames.append(KernelEvent(epoch="e1", cursor=len(frames) + 1, kind=kind, time=1, data=data))
        self._changed[session_id].set()

    async def send(self, session_id, turn):
        if session_id not in self._frames:
            raise SessionNotFound("session_not_found", "gone", 404)
        self.sent.append((session_id, turn.text))
        n = sum(1 for sid, _ in self.sent if sid == session_id)
        reply = self.responder(session_id, turn.text, n)
        if reply is LOSE:
            raise SessionNotFound("session_not_found", "gone", 404)
        if reply is APPROVAL:
            self._push(session_id, "approval/requested", {"id": "ap1", "toolName": "kw_write"})
            return f"m{n}"
        for kind, data in _frames(n, reply if isinstance(reply, str) else None):
            self._push(session_id, kind, data)
        return f"m{n}"

    async def subscribe(self, session_id, *, after=0, epoch=None):
        while True:
            frames = self._frames.get(session_id, [])
            for frame in frames[after:]:
                after = frame.cursor
                yield frame
            changed = self._changed[session_id]
            changed.clear()
            if len(self._frames.get(session_id, [])) <= after:
                await changed.wait()

    async def cancel(self, session_id):
        self.cancelled.append(session_id)
        return CancelResult(settled=True, status="idle")

    async def dispose(self, session_id):
        self.disposed.append(session_id)

    async def pending_approvals(self, session_id):
        return []

    async def decide_approval(self, session_id, approval_id, allow):
        return None


@dataclass
class Env:
    access: AccessService
    skills: object
    tools: ToolBox
    tool_service: object
    kernel: ScriptedKernel
    workflows: object
    sessionmaker: object

    def rebuild(self, kernel: ScriptedKernel | None = None):
        """模拟另一个进程（或重启后的业务服务）：同一个库，新的引擎与驱动者。"""
        return build_workflows(self.sessionmaker, self.tool_service, kernel or self.kernel,
                               self.skills.delivery.catalog, heartbeat_seconds=0.05)


@pytest.fixture
async def env(workflows_db, tmp_path):
    engine = make_engine(workflows_db)
    sessionmaker = make_sessionmaker(engine)
    access = AccessService(sessionmaker)
    assert await access.reload()
    await access.create_workspace(workspace_id="ws1", name="空间一", owner_user_id="alice")
    for user, role in (("dave", "editor"), ("erin", "editor")):
        await access.add_member("alice", "ws1", user, role)
    skills = build_skills(MASTER, sessionmaker, access, LocalBlobStore(tmp_path / "blobs"))
    box = ToolBox()
    tool_service = build_tool_service(MASTER, sessionmaker, registry=box.registry())
    kernel = ScriptedKernel()
    workflows = build_workflows(sessionmaker, tool_service, kernel, skills.delivery.catalog, heartbeat_seconds=0.05)
    yield Env(access=access, skills=skills, tools=box, tool_service=tool_service, kernel=kernel,
              workflows=workflows, sessionmaker=sessionmaker)
    await workflows.engine.aclose()
    await engine.dispose()


def actor(user: str = "dave") -> Actor:
    return Actor(user_id=user, workspace_id="ws1")


SKILL_MD = """---
name: {name}
description: 合同复核流程
---

按流程复核合同。
"""

REVIEW = """
version: 1
inputs:
  type: object
  properties: {contract_id: {type: string}}
  required: [contract_id]
steps:
  - key: extract
    type: tool
    tool: kw_extract
    args: {id: "${inputs.contract_id}"}
  - key: summarize
    type: agent
    prompt: "复核合同 ${inputs.contract_id}"
    tools: [kw_search]
    input: "${steps.extract.output.rows}"
    output_schema: {type: object, properties: {summary: {type: string}}, required: [summary]}
  - key: confirm
    type: approval
    title: 确认复核意见
    summary: "${steps.summarize.output.summary}"
  - key: save
    type: tool
    tool: kw_write
    args: {text: "${steps.summarize.output.summary}"}
output: {saved: "${steps.save.output}", summary: "${steps.summarize.output.summary}"}
"""


def workflow_zip(workflow: str | None, name: str = "contract-review") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("SKILL.md", SKILL_MD.format(name=name))
        if workflow is not None:
            archive.writestr("workflow.yaml", workflow)
    return buffer.getvalue()


async def publish(env: Env, workflow: str | None = REVIEW, *, user: str = "dave", name: str = "contract-review") -> str:
    """装一个个人技能（上传即发布），返回技能 id。"""
    service = env.skills.service
    view = await service.install(SkillActor(user_id=user, workspace_id="ws1"), "personal", workflow_zip(workflow, name))
    return view.id


async def settle(env_or_workflows, run_id: str, *, statuses=("succeeded", "failed", "cancelled", "waiting_approval",
                                                               "interrupted"), timeout: float = 5.0):
    """等运行到达给定状态之一，返回快照。"""
    workflows = getattr(env_or_workflows, "workflows", env_or_workflows)
    async with asyncio.timeout(timeout):
        while True:
            view = await workflows.store.load("ws1", run_id)
            if view.status in statuses:
                await workflows.engine.wait(run_id)
                return await workflows.store.load("ws1", run_id)
            await asyncio.sleep(0.02)


async def until(check, timeout: float = 5.0) -> None:
    """轮询直到 check()（可以是协程函数）为真。"""
    async with asyncio.timeout(timeout):
        while True:
            result = check()
            if asyncio.iscoroutine(result):
                result = await result
            if result:
                return
            await asyncio.sleep(0.02)

