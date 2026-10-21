"""用户组。成员写成 g 规则 user -> group:<组>。"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from . import rules as R
from . import store
from .core import Tx
from .errors import Conflict, NotFound
from .models import UserGroup, UserGroupMember
from .org import OrgMixin
from .vocab import Action, clean_name, validate_id


@dataclass(frozen=True)
class GroupView:
    id: str
    name: str
    members: tuple[str, ...]


class GroupsMixin(OrgMixin):
    async def _group(self, tx: Tx, group_id: str) -> UserGroup:
        validate_id(group_id, "用户组 id")
        group = await tx.session.get(UserGroup, group_id)
        if group is None or group.workspace_id != tx.workspace_id:
            raise NotFound("用户组不存在")
        return group

    async def create_group(self, actor_id: str, workspace_id: str, name: str) -> str:
        self._require(actor_id, workspace_id, Action.EDIT)
        name = clean_name(name)
        group_id = uuid.uuid4().hex
        try:
            async with self._tx(workspace_id) as tx:
                tx.session.add(UserGroup(id=group_id, workspace_id=workspace_id, name=name))
                await tx.session.flush()
        except IntegrityError as error:
            raise Conflict("同名用户组已存在") from error
        return group_id

    async def rename_group(self, actor_id: str, workspace_id: str, group_id: str, name: str) -> None:
        self._require(actor_id, workspace_id, Action.EDIT)
        name = clean_name(name)
        try:
            async with self._tx(workspace_id) as tx:
                (await self._group(tx, group_id)).name = name
                await tx.session.flush()
        except IntegrityError as error:
            raise Conflict("同名用户组已存在") from error

    async def delete_group(self, actor_id: str, workspace_id: str, group_id: str) -> None:
        """删除用户组：成员关系与授给该组的权限一并删除。"""
        self._require(actor_id, workspace_id, Action.EDIT)
        async with self._tx(workspace_id) as tx:
            group = await self._group(tx, group_id)
            await tx.remove_rules(*await store.rules_touching(tx.session, workspace_id, R.group_key(group_id)))
            await tx.session.delete(group)

    async def add_group_member(self, actor_id: str, workspace_id: str, group_id: str, user_id: str) -> None:
        self._require(actor_id, workspace_id, Action.EDIT)
        async with self._tx(workspace_id) as tx:
            await self._group(tx, group_id)
            await self._require_member(tx, user_id)
            if await tx.session.get(UserGroupMember, (group_id, user_id)) is not None:
                return
            tx.session.add(UserGroupMember(group_id=group_id, user_id=user_id, workspace_id=workspace_id))
            await tx.add_rules(R.group_member(user_id, group_id, workspace_id))

    async def remove_group_member(self, actor_id: str, workspace_id: str, group_id: str, user_id: str) -> None:
        self._require(actor_id, workspace_id, Action.EDIT)
        async with self._tx(workspace_id) as tx:
            await self._group(tx, group_id)
            existing = await tx.session.get(UserGroupMember, (group_id, validate_id(user_id, "user_id")))
            if existing is None:
                raise NotFound("该用户不在此用户组")
            await tx.session.delete(existing)
            await tx.remove_rules(R.group_member(user_id, group_id, workspace_id))

    async def list_groups(self, actor_id: str, workspace_id: str) -> tuple[GroupView, ...]:
        self._require(actor_id, workspace_id, Action.USE)
        async with self._read(workspace_id) as session:
            groups = (await session.scalars(select(UserGroup).where(UserGroup.workspace_id == workspace_id)
                                            .order_by(UserGroup.name))).all()
            members = (await session.scalars(select(UserGroupMember)
                                             .where(UserGroupMember.workspace_id == workspace_id)
                                             .order_by(UserGroupMember.user_id))).all()
        by_group: dict[str, list[str]] = {}
        for m in members:
            by_group.setdefault(m.group_id, []).append(m.user_id)
        return tuple(GroupView(g.id, g.name, tuple(by_group.get(g.id, ()))) for g in groups)
