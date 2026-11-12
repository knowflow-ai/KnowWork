"""用户组。成员写成 g 规则 user -> group:<组>。"""

import uuid
from collections.abc import Collection
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

    async def sync_managed_groups(self, workspace_id: str, user_id: str, *, managed: Collection[str],
                                  wanted: Collection[str]) -> None:
        """系统操作（网页嵌入的认证访问按接入方系统给的组，channels/web_identity.py）：把这个人在 managed 这几个用户组里的
        成员身份同步成 wanted，别的组一概不碰。组已经删了的跳过；人不是空间成员时什么也不做。"""
        validate_id(user_id, "user_id")
        managed_ids = {validate_id(group_id, "用户组 id") for group_id in managed}
        if not managed_ids:
            return
        targets = set(wanted) & managed_ids
        async with self._tx(workspace_id) as tx:
            try:
                await self._require_member(tx, user_id)
            except Conflict:
                return
            alive = set((await tx.session.scalars(select(UserGroup.id).where(
                UserGroup.workspace_id == workspace_id, UserGroup.id.in_(managed_ids)))).all())
            rows = (await tx.session.scalars(select(UserGroupMember).where(
                UserGroupMember.workspace_id == workspace_id, UserGroupMember.user_id == user_id,
                UserGroupMember.group_id.in_(managed_ids)))).all()
            current = {row.group_id: row for row in rows}
            for group_id in sorted((targets & alive) - set(current)):
                tx.session.add(UserGroupMember(group_id=group_id, user_id=user_id, workspace_id=workspace_id))
                await tx.add_rules(R.group_member(user_id, group_id, workspace_id))
            for group_id in sorted(set(current) - targets):
                await tx.session.delete(current[group_id])
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
