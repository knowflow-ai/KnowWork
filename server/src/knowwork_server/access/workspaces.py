"""工作空间本身：建、列出、改名、关闭（软删除）。

- 默认空间由初始化向导建，开源版只有它；新建、改名、关闭空间的接口由扩展提供（按授权开启），规则在这里；
- 关闭是软删除：空间与数据都留着，但权限规则不再加载、判定一律拒绝（系统管理员也进不去），成员立即失去访问；
- 默认空间不能关闭：全平台共用的配置（模型、文档解析等）存在它名下。
"""

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from . import rules as R
from .core import AccessCore
from .errors import AccessDenied, Conflict, NotFound
from .models import Workspace, WorkspaceMember
from .vocab import WORKSPACE_RESOURCE, Action, Role, clean_name, validate_id

DEFAULT_WORKSPACE_ID = "default"
# 全平台共用的配置（模型、文档解析、结构增强）存在默认空间名下：表结构不用动，单空间部署的数据原样就是平台配置。
PLATFORM_WORKSPACE_ID = DEFAULT_WORKSPACE_ID


@dataclass(frozen=True)
class WorkspaceView:
    id: str
    name: str
    role: str  # 调用者在这个空间里的角色；系统管理员不是成员也按所有者算


@dataclass(frozen=True)
class WorkspaceSummary:
    """系统管理员看的空间列表：每个空间是谁在负责。"""

    id: str
    name: str
    created_at: datetime
    owners: tuple[str, ...]


class WorkspacesMixin(AccessCore):
    async def create_workspace(self, *, workspace_id: str, name: str, owner_user_id: str) -> None:
        """建空间并设置首个所有者。系统操作，不判调用者：初始化向导建默认空间、open_workspace 都走这里。"""
        validate_id(workspace_id, "workspace_id")
        validate_id(owner_user_id, "user_id")
        name = clean_name(name)
        try:
            async with self._tx(workspace_id) as tx:
                tx.session.add(Workspace(id=workspace_id, name=name))
                await tx.session.flush()
                tx.session.add(WorkspaceMember(workspace_id=workspace_id, user_id=owner_user_id, role=Role.OWNER))
                await tx.session.flush()
                await tx.add_rules(*R.default_role_policies(workspace_id),
                                   R.member_role(owner_user_id, Role.OWNER, workspace_id))
        except IntegrityError as error:
            raise Conflict("工作空间已存在") from error

    async def ensure_default_workspace(self, owner_user_id: str) -> str:
        """默认空间；已存在则不动。"""
        async with self._sessionmaker() as session:
            exists = await session.get(Workspace, DEFAULT_WORKSPACE_ID)
        if exists is None:
            try:
                await self.create_workspace(workspace_id=DEFAULT_WORKSPACE_ID, name="默认空间",
                                            owner_user_id=owner_user_id)
            except Conflict:
                pass  # 并发创建，另一方已建好
        return DEFAULT_WORKSPACE_ID

    async def open_workspace(self, actor_id: str, *, name: str, owner_user_id: str) -> str:
        """系统管理员新建一个空间并指定所有者，返回空间编号。"""
        self._require_system_admin(actor_id)
        workspace_id = f"ws_{secrets.token_hex(6)}"
        await self.create_workspace(workspace_id=workspace_id, name=name, owner_user_id=owner_user_id)
        return workspace_id

    async def my_workspaces(self, user_id: str) -> tuple[WorkspaceView, ...]:
        """调用者能进的空间（前端切换空间、决定当前空间用）。成员表按空间开了行级安全，一次查不了所有空间，
        所以列出全部空间（全局表，数量很少）再按内存里的权限规则筛；系统管理员能进全部（关闭的除外）。"""
        validate_id(user_id, "user_id")
        return tuple(WorkspaceView(id=row.id, name=row.name, role=self._role_in(user_id, row.id))
                     for row in await self._open_rows()
                     if self.check(user_id, row.id, WORKSPACE_RESOURCE, Action.USE))

    async def all_workspaces(self, actor_id: str) -> tuple[WorkspaceSummary, ...]:
        """系统管理员看全部没关闭的空间与各自的所有者。"""
        self._require_system_admin(actor_id)
        found = []
        for row in await self._open_rows():
            async with self._read(row.id) as session:
                owners = (await session.scalars(select(WorkspaceMember.user_id).where(
                    WorkspaceMember.workspace_id == row.id, WorkspaceMember.role == Role.OWNER)
                    .order_by(WorkspaceMember.user_id))).all()
            found.append(WorkspaceSummary(id=row.id, name=row.name, created_at=row.created_at, owners=tuple(owners)))
        return tuple(found)

    async def rename_workspace(self, actor_id: str, workspace_id: str, name: str) -> None:
        """改名：空间所有者或系统管理员。"""
        validate_id(workspace_id, "workspace_id")
        self._require(actor_id, workspace_id, Action.OWNER)
        clean = clean_name(name)
        async with self._sessionmaker() as session, session.begin():
            row = await session.get(Workspace, workspace_id, with_for_update=True)
            if row is None or row.deleted_at is not None:
                raise NotFound("工作空间不存在")
            row.name = clean

    async def close_workspace(self, actor_id: str, workspace_id: str) -> None:
        """关闭（软删除）：系统管理员；默认空间不能关。关闭后整体重载权限，成员立即进不去。"""
        self._require_system_admin(actor_id)
        validate_id(workspace_id, "workspace_id")
        if workspace_id == DEFAULT_WORKSPACE_ID:
            raise Conflict("默认空间不能删除：全平台共用的配置存在它名下")
        async with self._sessionmaker() as session, session.begin():
            row = await session.get(Workspace, workspace_id, with_for_update=True)
            if row is None or row.deleted_at is not None:
                raise NotFound("工作空间不存在")
            row.deleted_at = datetime.now(UTC)
        await self.reload()

    def _require_system_admin(self, actor_id: str) -> None:
        if not (self.is_system_admin(actor_id) and self._engine.healthy):
            raise AccessDenied()

    async def _open_rows(self) -> list[Workspace]:
        async with self._sessionmaker() as session:
            return list((await session.scalars(select(Workspace).where(Workspace.deleted_at.is_(None))
                                               .order_by(Workspace.created_at, Workspace.id))).all())

    def _role_in(self, user_id: str, workspace_id: str) -> str:
        if self.check(user_id, workspace_id, WORKSPACE_RESOURCE, Action.OWNER):
            return Role.OWNER
        if self.check(user_id, workspace_id, WORKSPACE_RESOURCE, Action.EDIT):
            return Role.ADMIN
        return Role.EDITOR
