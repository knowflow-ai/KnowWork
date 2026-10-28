"""空间成员管理。成员角色以 g 规则 user -> role:<角色> 写进 Casbin。空间本身的建、改名、关闭见 workspaces.py。"""

from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from . import rules as R
from . import store
from .core import Tx
from .errors import Conflict, NotFound
from .models import DepartmentMember, UserGroupMember, Workspace, WorkspaceMember
from .vocab import Action, Role, parse_role, validate_id
from .workspaces import DEFAULT_WORKSPACE_ID, PLATFORM_WORKSPACE_ID, WorkspacesMixin

__all__ = ["DEFAULT_WORKSPACE_ID", "PLATFORM_WORKSPACE_ID", "MemberView", "MembersMixin"]

@dataclass(frozen=True)
class MemberView:
    user_id: str
    role: str


class MembersMixin(WorkspacesMixin):
    async def join_workspace(self, user_id: str, workspace_id: str, role: str = Role.EDITOR) -> bool:
        """系统操作：新注册的账号加入某个空间（凭邀请注册的进邀请所在的空间）。已是成员或空间不存在时什么也不做。

        只在建号时调用一次；管理员之后把人移出，不会因为再次登录被加回来。所有者不能这样加（由现任所有者任命）。
        """
        validate_id(user_id, "user_id")
        validate_id(workspace_id, "workspace_id")
        new_role = parse_role(role)
        if new_role == Role.OWNER:
            raise ValueError("不能以所有者身份自动加入")
        async with self._sessionmaker() as session:
            if await session.get(Workspace, workspace_id) is None:
                return False
        try:
            async with self._tx(workspace_id) as tx:
                tx.session.add(WorkspaceMember(workspace_id=workspace_id, user_id=user_id, role=new_role))
                await tx.session.flush()
                await tx.add_rules(R.member_role(user_id, new_role, workspace_id))
        except IntegrityError:
            return False  # 已是成员
        return True


    async def list_members(self, actor_id: str, workspace_id: str) -> tuple[MemberView, ...]:
        self._require(actor_id, workspace_id, Action.USE)
        async with self._read(workspace_id) as session:
            rows = (await session.scalars(select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace_id)
                                          .order_by(WorkspaceMember.user_id))).all()
        return tuple(MemberView(row.user_id, row.role) for row in rows)

    async def add_member(self, actor_id: str, workspace_id: str, user_id: str, role: str) -> MemberView:
        validate_id(user_id, "user_id")
        new_role = parse_role(role)
        self._require_role_manager(actor_id, workspace_id, new_role)
        try:
            async with self._tx(workspace_id) as tx:
                tx.session.add(WorkspaceMember(workspace_id=workspace_id, user_id=user_id, role=new_role))
                await tx.session.flush()
                await tx.add_rules(R.member_role(user_id, new_role, workspace_id))
        except IntegrityError as error:
            raise Conflict("该用户已是本空间成员") from error
        return MemberView(user_id, new_role)

    async def set_member_role(self, actor_id: str, workspace_id: str, user_id: str, role: str) -> MemberView:
        new_role = parse_role(role)
        self._require_role_manager(actor_id, workspace_id, new_role)
        async with self._tx(workspace_id) as tx:
            member = await self._member(tx.session, workspace_id, user_id, lock=True)
            old_role = member.role
            self._require_role_manager(actor_id, workspace_id, old_role)
            if old_role == new_role:
                return MemberView(user_id, new_role)
            if old_role == Role.OWNER:
                await self._ensure_other_owner(tx, user_id)
            member.role = new_role
            await tx.remove_rules(R.member_role(user_id, old_role, workspace_id))
            await tx.add_rules(R.member_role(user_id, new_role, workspace_id))
        return MemberView(user_id, new_role)

    async def remove_member(self, actor_id: str, workspace_id: str, user_id: str) -> None:
        """移出成员：同时移出本空间的部门与用户组，撤销直接授给他的权限。"""
        self._require(actor_id, workspace_id, Action.EDIT)
        async with self._tx(workspace_id) as tx:
            member = await self._member(tx.session, workspace_id, user_id, lock=True)
            self._require_role_manager(actor_id, workspace_id, member.role)
            if member.role == Role.OWNER:
                await self._ensure_other_owner(tx, user_id)
            await tx.session.delete(member)
            for table in (DepartmentMember, UserGroupMember):
                await tx.session.execute(delete(table).where(table.workspace_id == workspace_id,
                                                             table.user_id == user_id))
            await tx.remove_rules(*await store.rules_touching(tx.session, workspace_id, R.user_key(user_id)))

    def _require_role_manager(self, actor_id: str, workspace_id: str, role: str) -> None:
        """管理成员需要 workspace:edit；涉及所有者角色还需要 workspace:owner。"""
        self._require(actor_id, workspace_id, Action.EDIT)
        if role == Role.OWNER:
            self._require(actor_id, workspace_id, Action.OWNER)

    async def _ensure_other_owner(self, tx: Tx, user_id: str) -> None:
        owners = await tx.session.scalar(
            select(func.count()).select_from(
                select(WorkspaceMember.user_id).where(WorkspaceMember.workspace_id == tx.workspace_id,
                                                      WorkspaceMember.role == Role.OWNER,
                                                      WorkspaceMember.user_id != user_id)
                .with_for_update().subquery()))
        if not owners:
            raise Conflict("空间至少要保留一位所有者")

    async def _require_member(self, tx: Tx, user_id: str) -> None:
        validate_id(user_id, "user_id")
        try:
            await self._member(tx.session, tx.workspace_id, user_id)
        except NotFound as error:
            raise Conflict("该用户不是本空间成员，请先加入空间") from error

