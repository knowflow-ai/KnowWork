"""工作空间、组织结构与策略表。

除 workspace 自身外每张表都带 workspace_id，供行级安全按空间隔离。user_id 暂不加外键，
用户表由账号线提供，整合时补。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base

ID = String(64)


class Workspace(Base):
    __tablename__ = "workspace"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # 关闭（软删除）的时间：关闭后权限规则不再加载、谁都进不去，数据留着（见 workspaces.py）
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkspaceMember(Base):
    __tablename__ = "workspace_member"

    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ID, primary_key=True)
    role: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Department(Base):
    __tablename__ = "department"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("department.id", ondelete="RESTRICT"), index=True)
    name: Mapped[str] = mapped_column(String(100))


class DepartmentMember(Base):
    __tablename__ = "department_member"

    department_id: Mapped[str] = mapped_column(ForeignKey("department.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)


class UserGroup(Base):
    __tablename__ = "user_group"
    __table_args__ = (UniqueConstraint("workspace_id", "name"),)

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))


class UserGroupMember(Base):
    __tablename__ = "user_group_member"

    group_id: Mapped[str] = mapped_column(ForeignKey("user_group.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)


class CasbinRule(Base):
    """Casbin 策略。p 行 v0..v3 = sub, dom, obj, act；g 行 v0..v2 = 主体, 上级, 域。

    比官方适配器多一列 workspace_id，使策略表也受行级安全保护。
    """

    __tablename__ = "casbin_rule"
    __table_args__ = (
        UniqueConstraint("ptype", "v0", "v1", "v2", "v3", name="uk_casbin_rule"),
        Index("ix_casbin_rule_workspace", "workspace_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"))
    ptype: Mapped[str] = mapped_column(String(8))
    v0: Mapped[str] = mapped_column(String(160))
    v1: Mapped[str] = mapped_column(String(160))
    v2: Mapped[str] = mapped_column(String(160))
    v3: Mapped[str] = mapped_column(String(160), default="")


ACCESS_TABLES = tuple(
    model.__table__ for model in (Workspace, WorkspaceMember, Department, DepartmentMember, UserGroup, UserGroupMember,
                                  CasbinRule)
)
# 受行级安全保护的表：全部带 workspace_id 的表
ACCESS_RLS_TABLES = ("workspace_member", "department", "department_member", "user_group", "user_group_member",
                     "casbin_rule")
