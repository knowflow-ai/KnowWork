"""技能运行事实的写入接口（技术方案 6.6 节）。第一期只有表与写入，查询与验证台由后续线做。

两种写法：
- 边跑边写（固定工作流这类由业务服务驱动的运行）：start 时 status = running，record_step 逐步写，finish 写终态、
  耗时与成本；终态只能写一次；每一步一条 skill_step_run，(skill_run_id, seq) 唯一，重复写同一步视为冲突；
- 回合结束后整轮替换（对话里的技能，由 projector 从会话日志投影）：replace_turn 先删这一轮已有的再写，
  同一轮算几次结果都一样，可以随时重算。
失败归到固定的几个阶段：选择、权限、步骤、工具、知识、生成、交付（L14、L15）。
写入前不存任何正文与凭证：只存工具名、状态、耗时、产物引用。
"""

import uuid
from collections.abc import Collection, Sequence
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..access.rls import set_workspace
from .errors import SkillConflict, SkillNotFound
from .models import Skill, SkillRun, SkillStepRun
from .service import scope_key

FailureStage = Literal["selection", "permission", "step", "tool", "knowledge", "generation", "delivery"]
_ID = r"^[A-Za-z0-9_.:-]{1,128}$"


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RunStart(_Frozen):
    workspace_id: str = Field(pattern=_ID)
    run_id: str = Field(pattern=_ID)
    session_id: str = Field(pattern=_ID)
    user_id: str = Field(pattern=_ID)
    skill_id: str = Field(pattern=_ID)
    skill_version: str = Field(min_length=1, max_length=64)
    selection_mode: Literal["manual", "automatic", "workflow"]


class StepRecord(_Frozen):
    step_key: str = Field(min_length=1, max_length=128)
    step_type: Literal["tool", "model", "approval", "workflow"]
    seq: int = Field(ge=0, le=100_000)
    status: Literal["succeeded", "failed", "skipped"]
    duration_ms: int = Field(default=0, ge=0)
    tool_name: str | None = Field(default=None, max_length=64)
    approval_result: Literal["approved", "rejected", "expired"] | None = None
    artifact_refs: tuple[str, ...] = Field(default=(), max_length=64)


class RunFinish(_Frozen):
    status: Literal["succeeded", "failed", "cancelled"]
    total_tokens: int = Field(default=0, ge=0)
    cost: float = Field(default=0, ge=0)
    artifact_count: int = Field(default=0, ge=0)
    failure_stage: FailureStage | None = None
    failure_code: str | None = Field(default=None, max_length=64)


class TurnRun(_Frozen):
    """投影出来的一次完整运行：开始、结束与全部步骤一起写。"""

    start: RunStart
    started_at: datetime
    ended_at: datetime
    finish: RunFinish
    steps: tuple[StepRecord, ...] = Field(default=(), max_length=10_000)


class SkillRunStore:
    def __init__(self, sessionmaker: async_sessionmaker) -> None:
        self._sessionmaker = sessionmaker

    async def start(self, run: RunStart) -> str:
        skill_run_id = uuid.uuid4().hex
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, run.workspace_id)
            session.add(SkillRun(id=skill_run_id, status="running", **run.model_dump()))
        return skill_run_id

    async def record_step(self, workspace_id: str, skill_run_id: str, step: StepRecord) -> str:
        step_id = uuid.uuid4().hex
        values = {**step.model_dump(), "artifact_refs": list(step.artifact_refs)}
        try:
            async with self._sessionmaker() as session, session.begin():
                await set_workspace(session, workspace_id)
                if await session.get(SkillRun, skill_run_id) is None:
                    raise SkillNotFound("运行记录不存在")
                session.add(SkillStepRun(id=step_id, workspace_id=workspace_id, skill_run_id=skill_run_id, **values))
        except IntegrityError as error:
            raise SkillConflict("step_exists", f"第 {step.seq} 步已经记录过") from error
        return step_id

    async def finish(self, workspace_id: str, skill_run_id: str, result: RunFinish) -> None:
        if result.status == "failed" and result.failure_stage is None:
            raise ValueError("失败的运行必须给出失败阶段")
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            updated = await session.execute(
                update(SkillRun).where(SkillRun.id == skill_run_id, SkillRun.status == "running")
                .values(ended_at=datetime.now(UTC), **result.model_dump()))
            if updated.rowcount == 0:
                found = await session.get(SkillRun, skill_run_id)
                if found is None:
                    raise SkillNotFound("运行记录不存在")
                raise SkillConflict("run_finished", "这次运行已经结束")

    async def replace_turn(self, workspace_id: str, run_id: str, runs: Sequence[TurnRun]) -> None:
        """整轮替换：删掉 run_id 下已有的运行（步骤随外键级联删除）再写入。runs 为空就是清掉这一轮。

        同一轮可能同时被回合结束的投影和人工重算写，先拿按 run_id 的事务级咨询锁，免得两边各删各写出重复。
        """
        for run in runs:
            if (run.start.workspace_id, run.start.run_id) != (workspace_id, run_id):
                raise ValueError("运行记录不属于这一轮")
            if run.finish.status == "failed" and run.finish.failure_stage is None:
                raise ValueError("失败的运行必须给出失败阶段")
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            await session.execute(select(func.pg_advisory_xact_lock(func.hashtext(f"skill_run:{run_id}"))))
            await session.execute(delete(SkillRun).where(SkillRun.workspace_id == workspace_id,
                                                         SkillRun.run_id == run_id))
            for run in runs:
                skill_run_id = uuid.uuid4().hex
                session.add(SkillRun(id=skill_run_id, started_at=run.started_at, ended_at=run.ended_at,
                                     **run.start.model_dump(), **run.finish.model_dump()))
                session.add_all(SkillStepRun(id=uuid.uuid4().hex, workspace_id=workspace_id, skill_run_id=skill_run_id,
                                             **{**step.model_dump(), "artifact_refs": list(step.artifact_refs)})
                                for step in run.steps)

    async def skill_ids(self, workspace_id: str, user_id: str, names: Collection[str]) -> dict[str, str]:
        """技能名 → 技能 ID，与会话清单同一规则：空间技能优先，其次本人的个人技能。

        不看是否仍启用、已发布：这一轮确实加载过它，事后停用不影响记下这次运行。删掉了的技能查不到，调用方跳过。
        """
        if not names:
            return {}
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            rows = (await session.execute(select(Skill.name, Skill.id, Skill.scope).where(
                Skill.workspace_id == workspace_id, Skill.name.in_(tuple(names)),
                Skill.scope_key.in_(("workspace", scope_key("personal", user_id)))))).all()
        personal = {row.name: row.id for row in rows if row.scope != "workspace"}
        return {**personal, **{row.name: row.id for row in rows if row.scope == "workspace"}}
