"""工作流运行状态的读写。每次写都在声明了工作空间的事务里，受行级安全约束。

并发规则：
- 只有持有运行的驱动者（driver_id，且运行处于 running）能写步骤进度与结果：所有这类写都是带条件的
  UPDATE（比较并交换），条件不成立返回假，驱动者据此停下（运行已被取消、或被对账收走）；
- 运行或步骤每变化一次，workflow_run.version 加一，事件流据此判断要不要推送；
- 步骤到达终态时同时写一条不可变的 skill_step_run，运行到达终态时结算 skill_run（运行事实）。
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..access.models import Workspace
from ..access.rls import set_workspace
from ..skills.models import SkillRun, SkillStepRun
from .errors import FailureStage, RunConflict, RunNotFound
from .models import WorkflowRun, WorkflowStep, WorkflowStepCache
from .views import TERMINAL_RUN, RunView, run_view

ACTIVE_RUN = ("running", "waiting_approval", "interrupted")
FACT_TYPE = {"tool": "tool", "agent": "model", "approval": "approval"}
FACT_STATUS = {"succeeded": "succeeded", "failed": "failed", "skipped": "skipped"}


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class NewRun:
    workspace_id: str
    user_id: str
    skill_id: str
    skill_name: str
    skill_version: str
    release_id: str
    definition: dict[str, Any]
    definition_digest: str
    inputs: dict[str, Any]
    session_id: str | None = None


@dataclass(frozen=True)
class StepStart:
    key: str
    seq: int
    type: str
    cache_key: str | None = None
    tool_name: str | None = None


@dataclass(frozen=True)
class StepEnd:
    key: str
    seq: int
    type: str
    status: str  # succeeded | failed | skipped | interrupted | cancelled | waiting_approval
    output: Any = None
    output_digest: str | None = None
    cache_hit: bool = False
    cache_key: str | None = None
    tool_name: str | None = None
    action_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    approval_result: str | None = None
    decided_by: str | None = None


@dataclass(frozen=True)
class RunEnd:
    status: str  # succeeded | failed | cancelled
    output: Any = None
    failure_stage: FailureStage | None = None
    failure_code: str | None = None
    failure_message: str | None = None


class WorkflowStore:
    def __init__(self, sessionmaker: async_sessionmaker) -> None:
        self._sessionmaker = sessionmaker

    @asynccontextmanager
    async def _tx(self, workspace_id: str) -> AsyncIterator[AsyncSession]:
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            yield session

    # ---- 创建与读取 ----

    async def create(self, run: NewRun, driver_id: str) -> str:
        run_id = uuid.uuid4().hex
        async with self._tx(run.workspace_id) as session:
            session.add(SkillRun(id=run_id, workspace_id=run.workspace_id, run_id=f"wf.{run_id}",
                                 session_id=run.session_id or f"wf.{run_id}", user_id=run.user_id,
                                 skill_id=run.skill_id, skill_version=run.skill_version, selection_mode="workflow",
                                 status="running"))
            await session.flush()
            session.add(WorkflowRun(id=run_id, workspace_id=run.workspace_id, user_id=run.user_id,
                                    skill_id=run.skill_id, skill_name=run.skill_name, skill_version=run.skill_version,
                                    release_id=run.release_id, definition=run.definition,
                                    definition_digest=run.definition_digest, inputs=run.inputs, status="running",
                                    driver_id=driver_id, heartbeat_at=_now(), version=1))
        return run_id

    async def load(self, workspace_id: str, run_id: str) -> RunView:
        async with self._tx(workspace_id) as session:
            row = await session.get(WorkflowRun, run_id)
            if row is None or row.workspace_id != workspace_id:
                raise RunNotFound()
            steps = (await session.scalars(select(WorkflowStep).where(WorkflowStep.run_id == run_id))).all()
            return run_view(row, list(steps))

    async def version(self, workspace_id: str, run_id: str) -> int | None:
        async with self._tx(workspace_id) as session:
            return await session.scalar(select(WorkflowRun.version).where(WorkflowRun.id == run_id))

    async def list_for_user(self, workspace_id: str, user_id: str, limit: int) -> list[RunView]:
        async with self._tx(workspace_id) as session:
            rows = (await session.scalars(select(WorkflowRun).where(
                WorkflowRun.workspace_id == workspace_id, WorkflowRun.user_id == user_id)
                .order_by(WorkflowRun.created_at.desc()).limit(limit))).all()
            return [run_view(row, []) for row in rows]

    # ---- 驱动者的比较并交换 ----

    @staticmethod
    def _owned(run_id: str, driver_id: str):
        return (WorkflowRun.id == run_id, WorkflowRun.status == "running", WorkflowRun.driver_id == driver_id)

    @staticmethod
    async def _bump(session: AsyncSession, run_id: str, owner: str, values: dict[str, Any]) -> bool:
        """带「运行仍归 owner」条件更新运行，并把 version 加一。"""
        result = await session.execute(update(WorkflowRun).where(*WorkflowStore._owned(run_id, owner))
                                       .values(version=WorkflowRun.version + 1, heartbeat_at=_now(), **values))
        return result.rowcount == 1

    async def claim(self, workspace_id: str, run_id: str, from_status: str, driver_id: str) -> bool:
        async with self._tx(workspace_id) as session:
            result = await session.execute(update(WorkflowRun).where(
                WorkflowRun.id == run_id, WorkflowRun.status == from_status).values(
                status="running", driver_id=driver_id, heartbeat_at=_now(), failure_code=None,
                failure_message=None, version=WorkflowRun.version + 1))
            return result.rowcount == 1

    async def heartbeat(self, workspace_id: str, run_id: str, driver_id: str) -> bool:
        async with self._tx(workspace_id) as session:
            result = await session.execute(update(WorkflowRun).where(*self._owned(run_id, driver_id))
                                           .values(heartbeat_at=_now()))
            return result.rowcount == 1

    async def step_started(self, workspace_id: str, run_id: str, driver_id: str, step: StepStart) -> int | None:
        """把一步标为执行中并返回这是第几次尝试；运行已不归本驱动者时返回 None。"""
        async with self._tx(workspace_id) as session:
            if not await self._bump(session, run_id, driver_id, {"current_step": step.key}):
                return None
            statement = insert(WorkflowStep).values(
                id=uuid.uuid4().hex, workspace_id=workspace_id, run_id=run_id, step_key=step.key, seq=step.seq,
                step_type=step.type, status="running", attempts=1, cache_key=step.cache_key,
                tool_name=step.tool_name, started_at=_now())
            statement = statement.on_conflict_do_update(
                constraint="uk_workflow_step_key",
                set_={"status": "running", "attempts": WorkflowStep.attempts + 1, "cache_key": step.cache_key,
                      "started_at": _now(), "ended_at": None, "error_code": None, "error_message": None},
            ).returning(WorkflowStep.attempts)
            return (await session.execute(statement)).scalar_one()

    async def step_finished(self, workspace_id: str, run_id: str, driver_id: str, end: StepEnd, *,
                            run_status: str | None = None) -> bool:
        """写一步的结果；run_status 给出时同时把运行改成它（等待审批、中断时交出驱动权）。"""
        async with self._tx(workspace_id) as session:
            release = {} if run_status is None else {"status": run_status, "driver_id": None}
            if not await self._bump(session, run_id, driver_id, {"current_step": end.key, **release}):
                return False
            await self._write_step(session, workspace_id, run_id, end)
            return True

    async def _write_step(self, session: AsyncSession, workspace_id: str, run_id: str, end: StepEnd) -> None:
        terminal = end.status in FACT_STATUS
        values = {"status": end.status, "output": end.output, "output_digest": end.output_digest,
                  "cache_hit": end.cache_hit, "tool_name": end.tool_name, "action_id": end.action_id,
                  "error_code": end.error_code, "error_message": end.error_message,
                  "approval_result": end.approval_result, "decided_by": end.decided_by,
                  "ended_at": _now() if terminal else None}
        if end.cache_key is not None:
            values["cache_key"] = end.cache_key
        statement = insert(WorkflowStep).values(
            id=uuid.uuid4().hex, workspace_id=workspace_id, run_id=run_id, step_key=end.key, seq=end.seq,
            step_type=end.type, attempts=0, started_at=_now(), **values)
        await session.execute(statement.on_conflict_do_update(constraint="uk_workflow_step_key", set_=values))
        if terminal:
            await self._fact(session, workspace_id, run_id, end)

    @staticmethod
    async def _fact(session: AsyncSession, workspace_id: str, run_id: str, end: StepEnd) -> None:
        started = await session.scalar(select(WorkflowStep.started_at).where(
            WorkflowStep.run_id == run_id, WorkflowStep.step_key == end.key))
        duration = int((_now() - started).total_seconds() * 1000) if started else 0
        await session.execute(insert(SkillStepRun).values(
            id=uuid.uuid4().hex, workspace_id=workspace_id, skill_run_id=run_id, step_key=end.key,
            step_type=FACT_TYPE[end.type], seq=end.seq, status=FACT_STATUS[end.status], duration_ms=max(duration, 0),
            tool_name=end.tool_name, approval_result=end.approval_result, artifact_refs=[],
        ).on_conflict_do_nothing(constraint="uk_skill_step_seq"))

    async def finish(self, workspace_id: str, run_id: str, driver_id: str, end: RunEnd) -> bool:
        async with self._tx(workspace_id) as session:
            if not await self._bump(session, run_id, driver_id, _terminal_values(end)):
                return False
            await self._settle_fact(session, run_id, end)
            return True

    @staticmethod
    async def _settle_fact(session: AsyncSession, run_id: str, end: RunEnd) -> None:
        await session.execute(update(SkillRun).where(SkillRun.id == run_id, SkillRun.status == "running").values(
            status=end.status, ended_at=_now(), failure_stage=end.failure_stage, failure_code=end.failure_code))

    # ---- 外部操作：审批、取消 ----

    async def _locked(self, session: AsyncSession, run_id: str) -> WorkflowRun:
        row = await session.scalar(select(WorkflowRun).where(WorkflowRun.id == run_id).with_for_update())
        if row is None:
            raise RunNotFound()
        return row

    async def decide(self, workspace_id: str, run_id: str, approval: StepEnd, driver_id: str) -> RunView:
        """审批门的决定。批准：步骤成功、运行交给 driver_id 继续；拒绝：步骤失败、整个运行以失败结束。"""
        async with self._tx(workspace_id) as session:
            row = await self._locked(session, run_id)
            waiting = await session.scalar(select(WorkflowStep.status).where(
                WorkflowStep.run_id == run_id, WorkflowStep.step_key == approval.key))
            if row.status != "waiting_approval" or row.current_step != approval.key or waiting != "waiting_approval":
                raise RunConflict("not_waiting_approval", "这一步当前不在等待审批")
            await self._write_step(session, workspace_id, run_id, approval)
            if approval.status == "succeeded":
                row.status, row.driver_id, row.heartbeat_at = "running", driver_id, _now()
            else:
                end = RunEnd(status="failed", failure_stage="step", failure_code="approval_rejected",
                             failure_message="审批被拒绝，运行已结束")
                for key, value in _terminal_values(end).items():
                    setattr(row, key, value)
                await self._settle_fact(session, run_id, end)
            row.version += 1
        return await self.load(workspace_id, run_id)

    async def cancel(self, workspace_id: str, run_id: str) -> bool:
        async with self._tx(workspace_id) as session:
            row = await self._locked(session, run_id)
            if row.status in TERMINAL_RUN:
                return False
            end = RunEnd(status="cancelled", failure_code="cancelled")
            for key, value in _terminal_values(end).items():
                setattr(row, key, value)
            row.version += 1
            await session.execute(update(WorkflowStep).where(
                WorkflowStep.run_id == run_id, WorkflowStep.status.in_(("running", "waiting_approval", "interrupted")))
                .values(status="cancelled", ended_at=_now()))
            await self._settle_fact(session, run_id, end)
            return True

    async def interrupt(self, workspace_id: str, run_id: str, driver_id: str, code: str, message: str) -> bool:
        """驱动者自己停下（进程关闭、宿主重启、动作仍在执行）：运行与执行中的步骤标为中断，可续跑。"""
        async with self._tx(workspace_id) as session:
            values = {"status": "interrupted", "driver_id": None, "failure_code": code[:64],
                      "failure_message": message[:500]}
            if not await self._bump(session, run_id, driver_id, values):
                return False
            await self._interrupt_steps(session, run_id)
            return True

    @staticmethod
    async def _interrupt_steps(session: AsyncSession, run_id: str) -> None:
        await session.execute(update(WorkflowStep).where(WorkflowStep.run_id == run_id,
                                                         WorkflowStep.status == "running")
                              .values(status="interrupted"))

    # ---- 启动对账 ----

    async def reconcile(self, stale_after: timedelta) -> list[tuple[str, str]]:
        """把心跳超时、仍记为 running 的运行标为中断；返回（工作空间，运行）列表。等待审批的运行不动。"""
        cutoff = _now() - stale_after
        async with self._sessionmaker() as session:
            workspaces = list((await session.execute(select(Workspace.id))).scalars())
        swept: list[tuple[str, str]] = []
        for workspace_id in workspaces:
            async with self._tx(workspace_id) as session:
                ids = (await session.scalars(update(WorkflowRun).where(
                    WorkflowRun.status == "running",
                    (WorkflowRun.heartbeat_at.is_(None)) | (WorkflowRun.heartbeat_at <= cutoff))
                    .values(status="interrupted", driver_id=None, failure_code="interrupted",
                            failure_message="业务服务重启，运行被中断，可以续跑", version=WorkflowRun.version + 1)
                    .returning(WorkflowRun.id))).all()
                for run_id in ids:
                    await self._interrupt_steps(session, run_id)
            swept.extend((workspace_id, run_id) for run_id in ids)
        return swept

    # ---- 步骤结果缓存 ----

    async def cached(self, workspace_id: str, cache_key: str, ttl: timedelta) -> WorkflowStepCache | None:
        async with self._tx(workspace_id) as session:
            row = await session.get(WorkflowStepCache, (workspace_id, cache_key))
            if row is None or row.created_at < _now() - ttl:
                return None
            return row

    async def remember(self, workspace_id: str, cache_key: str, output: Any, output_digest: str) -> None:
        async with self._tx(workspace_id) as session:
            await session.execute(insert(WorkflowStepCache).values(
                workspace_id=workspace_id, cache_key=cache_key, output=output, output_digest=output_digest,
                created_at=_now()).on_conflict_do_update(
                index_elements=["workspace_id", "cache_key"],
                set_={"output": output, "output_digest": output_digest, "created_at": _now()}))


def _terminal_values(end: RunEnd) -> dict[str, Any]:
    return {"status": end.status, "output": end.output, "failure_code": end.failure_code,
            "failure_message": end.failure_message, "driver_id": None, "ended_at": _now()}
