"""固定工作流的运行状态表（技术方案 6.5、6.6 节；E2 报告「要做得不一样的地方」）。

运行事实仍写 skills 的 skill_run / skill_step_run（验证台与统计只读那两张）：
- workflow_run 与 skill_run 一对一（同一个 id），存 skill_run 放不下的可变运行状态：流程定义快照、入参、
  当前状态（含 waiting_approval、interrupted 两个非终态）、驱动者与心跳、结果；
- workflow_step 每步一行，原地更新：尝试次数、缓存键、步骤结果、错误、审批决定。步骤到达终态时
  另写一条不可变的 skill_step_run；
- workflow_step_cache 按「工作空间 + 缓存键」存校验通过的步骤结果，跨运行复用。

为什么不直接给 skill_run 加列：skill_run 的状态机（running 到终态只写一次）已经被验证台依赖，
把 waiting_approval / interrupted 塞进去会改变它的语义；另外三张表全部带 workspace_id、开行级安全。
"""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base

ID = String(64)


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class WorkflowRun(Base):
    __tablename__ = "workflow_run"
    __table_args__ = (Index("ix_workflow_run_user", "workspace_id", "user_id", "created_at"),
                      Index("ix_workflow_run_status", "workspace_id", "status"))

    id: Mapped[str] = mapped_column(ForeignKey("skill_run.id", ondelete="CASCADE"), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"))
    user_id: Mapped[str] = mapped_column(ID)
    skill_id: Mapped[str] = mapped_column(ID)
    skill_name: Mapped[str] = mapped_column(String(64))
    skill_version: Mapped[str] = mapped_column(String(64))
    release_id: Mapped[str] = mapped_column(ID)
    definition: Mapped[dict] = mapped_column(JSONB)  # 不可变快照：续跑按它执行，不受之后发布的影响
    definition_digest: Mapped[str] = mapped_column(String(64))
    inputs: Mapped[dict] = mapped_column(JSONB)  # 已按入参 Schema 校验；续跑不需要调用方重给
    # running | waiting_approval | interrupted | succeeded | failed | cancelled
    status: Mapped[str] = mapped_column(String(16))
    current_step: Mapped[str | None] = mapped_column(String(48))
    output: Mapped[Any] = mapped_column(JSONB, nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64))
    failure_message: Mapped[str | None] = mapped_column(String(500))
    driver_id: Mapped[str | None] = mapped_column(ID)  # 正在推进这次运行的进程实例；非 running 时为空
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(BigInteger, default=0)  # 运行或任一步骤每变化一次加一，事件流据此推送
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkflowStep(Base):
    __tablename__ = "workflow_step"
    __table_args__ = (UniqueConstraint("run_id", "step_key", name="uk_workflow_step_key"),
                      UniqueConstraint("run_id", "seq", name="uk_workflow_step_seq"))

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_run.id", ondelete="CASCADE"))
    step_key: Mapped[str] = mapped_column(String(48))
    seq: Mapped[int] = mapped_column(Integer)
    step_type: Mapped[str] = mapped_column(String(16))  # tool | agent | approval
    # running | waiting_approval | succeeded | failed | skipped | interrupted | cancelled
    status: Mapped[str] = mapped_column(String(16))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    cache_key: Mapped[str | None] = mapped_column(String(64))
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    output: Mapped[Any] = mapped_column(JSONB, nullable=True)
    output_digest: Mapped[str | None] = mapped_column(String(64))
    tool_name: Mapped[str | None] = mapped_column(String(64))
    action_id: Mapped[str | None] = mapped_column(ID)  # 工具步骤对应的动作回执
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(500))
    approval_result: Mapped[str | None] = mapped_column(String(16))  # approved | rejected
    decided_by: Mapped[str | None] = mapped_column(ID)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkflowStepCache(Base):
    __tablename__ = "workflow_step_cache"

    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), primary_key=True)
    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    output: Mapped[Any] = mapped_column(JSONB, nullable=True)
    output_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = _now()


WORKFLOW_TABLES = tuple(model.__table__ for model in (WorkflowRun, WorkflowStep, WorkflowStepCache))
# 受行级安全保护的表（主线整合进迁移时一并开启）
WORKFLOW_RLS_TABLES = ("workflow_run", "workflow_step", "workflow_step_cache")
