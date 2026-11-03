"""工作空间、组织结构与策略表。

除 workspace 自身与企业组织（org_*，全系统一份）外每张表都带 workspace_id，供行级安全按空间隔离。
user_id 暂不加外键，用户表由账号线提供，整合时补。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
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
    # 怎么进来的：manual 管理员手工加、邀请、默认空间；rule 按成员规则从企业组织自动加，人离开部门时随之移出
    origin: Mapped[str] = mapped_column(String(16), default="manual", server_default="manual")


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


class OrgDepartment(Base):
    """企业组织的部门：各目录来源（AD、企业微信……）同步来，全系统一份，不属于某个空间、不开行级安全。
    按（来源, 外部 ID）认同一个部门：改名、移动保留 id 与授权；目录里没了软删（status=removed），回来了恢复。"""

    __tablename__ = "org_department"
    __table_args__ = (UniqueConstraint("source_id", "external_id", name="uq_org_department_external"),)

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    source_id: Mapped[str] = mapped_column(ID, index=True)
    external_id: Mapped[str] = mapped_column(String(255))
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("org_department.id", ondelete="SET NULL"),
                                                  nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    sort: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="active")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OrgMember(Base):
    """企业部门的成员（以目录为准，本地不改）。"""

    __tablename__ = "org_member"

    department_id: Mapped[str] = mapped_column(ForeignKey("org_department.id", ondelete="CASCADE"),
                                               primary_key=True)
    user_id: Mapped[str] = mapped_column(ID, primary_key=True, index=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)


class WorkspaceMemberRule(Base):
    """空间成员规则：某个企业部门（可含下级）的人自动是本空间成员、给这个角色。由空间管理员设。"""

    __tablename__ = "workspace_member_rule"
    __table_args__ = (UniqueConstraint("workspace_id", "department_id", name="uq_workspace_member_rule"),)

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)
    department_id: Mapped[str] = mapped_column(ForeignKey("org_department.id", ondelete="CASCADE"))
    include_children: Mapped[bool] = mapped_column(Boolean, default=True)
    role: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


ACCESS_TABLES = tuple(
    model.__table__ for model in (Workspace, WorkspaceMember, Department, DepartmentMember, UserGroup, UserGroupMember,
                                  CasbinRule, OrgDepartment, OrgMember, WorkspaceMemberRule)
)
# 受行级安全保护的表：全部带 workspace_id 的表（企业组织是全系统的，不在此列）
ACCESS_RLS_TABLES = ("workspace_member", "department", "department_member", "user_group", "user_group_member",
                     "casbin_rule", "workspace_member_rule")
