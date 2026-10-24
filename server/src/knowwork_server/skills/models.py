"""技能的表。全部带 workspace_id，受行级安全保护。

- skill：一个技能的可变控制面。草稿字段随安装、更新变化；发布字段指向最近一次发布；
  scope_key 是「workspace」或「user:<用户 id>」，同一范围内技能名唯一；
- skill_release：不可变的发布版本，(skill_id, version) 唯一；运行配置只引用它；
- skill_run、skill_step_run：运行事实，目前先有表与写入接口。skill_run 只记技能 id 与版本字符串，
  不加外键：技能删除后，历史运行事实仍要能查。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base

ID = String(64)


def _now_column() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class Skill(Base):
    __tablename__ = "skill"
    __table_args__ = (UniqueConstraint("workspace_id", "scope_key", "name", name="uk_skill_scope_name"),)

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)
    scope: Mapped[str] = mapped_column(String(16))  # personal | workspace
    scope_key: Mapped[str] = mapped_column(String(80))
    owner_id: Mapped[str] = mapped_column(ID)
    name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))  # draft | published
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    draft_digest: Mapped[str] = mapped_column(String(64))
    draft_manifest: Mapped[dict] = mapped_column(JSONB)
    draft_revision: Mapped[int] = mapped_column(Integer, default=1)
    has_unpublished_changes: Mapped[bool] = mapped_column(Boolean, default=True)
    published_version: Mapped[str | None] = mapped_column(String(64))
    published_digest: Mapped[str | None] = mapped_column(String(64))
    published_release_id: Mapped[str | None] = mapped_column(ID)
    published_manifest: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _now_column()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


class SkillRelease(Base):
    __tablename__ = "skill_release"
    __table_args__ = (UniqueConstraint("skill_id", "version", name="uk_skill_release_version"),
                      Index("ix_skill_release_digest", "skill_id", "archive_digest"))

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)
    skill_id: Mapped[str] = mapped_column(ForeignKey("skill.id", ondelete="CASCADE"))
    version: Mapped[str] = mapped_column(String(64))
    archive_digest: Mapped[str] = mapped_column(String(64))
    manifest: Mapped[dict] = mapped_column(JSONB)
    released_by: Mapped[str] = mapped_column(ID)
    released_at: Mapped[datetime] = _now_column()


class SkillRun(Base):
    __tablename__ = "skill_run"
    __table_args__ = (Index("ix_skill_run_skill", "workspace_id", "skill_id", "started_at"),)

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    session_id: Mapped[str] = mapped_column(String(128))
    user_id: Mapped[str] = mapped_column(ID)
    skill_id: Mapped[str] = mapped_column(ID)
    skill_version: Mapped[str] = mapped_column(String(64))
    selection_mode: Mapped[str] = mapped_column(String(16))  # manual | automatic | workflow
    status: Mapped[str] = mapped_column(String(16))  # running | succeeded | failed | cancelled
    started_at: Mapped[datetime] = _now_column()
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(Numeric(18, 6), default=0)
    artifact_count: Mapped[int] = mapped_column(Integer, default=0)
    failure_stage: Mapped[str | None] = mapped_column(String(16))
    failure_code: Mapped[str | None] = mapped_column(String(64))


class SkillStepRun(Base):
    __tablename__ = "skill_step_run"
    __table_args__ = (UniqueConstraint("skill_run_id", "seq", name="uk_skill_step_seq"),)

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)
    skill_run_id: Mapped[str] = mapped_column(ForeignKey("skill_run.id", ondelete="CASCADE"))
    step_key: Mapped[str] = mapped_column(String(128))
    step_type: Mapped[str] = mapped_column(String(16))  # tool | model | approval | workflow
    seq: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))  # succeeded | failed | skipped
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    tool_name: Mapped[str | None] = mapped_column(String(64))
    approval_result: Mapped[str | None] = mapped_column(String(16))
    artifact_refs: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = _now_column()


SKILL_TABLES = tuple(model.__table__ for model in (Skill, SkillRelease, SkillRun, SkillStepRun))
# 受行级安全保护的表（主线整合进迁移时一并开启）
SKILL_RLS_TABLES = ("skill", "skill_release", "skill_run", "skill_step_run")
