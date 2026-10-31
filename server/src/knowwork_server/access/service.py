"""AccessService：工作空间、成员、部门、用户组与资源授权的统一入口。

所有写操作都带调用者 actor_id，在服务端判定：成员、组织、授权的管理需要 workspace:edit
（空间 owner 与 admin），涉及所有者角色需要 workspace:owner。
"""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import rules as R
from . import store
from .core import Tx
from .errors import NotFound
from .groups import GroupsMixin
from .models import Department, UserGroup
from .rules import RuleChange
from .vocab import Action, InvalidInput, Resource, Subject, grantable, parse_action


@dataclass(frozen=True)
class GrantView:
    subject: str
    resource: str
    action: str


MAX_USERS = 200  # 「给谁用」一次最多选这么多对象


class AccessService(GroupsMixin):
    async def grant(self, actor_id: str, workspace_id: str, subject: str, resource: str, action: str) -> GrantView:
        """授权。同一主体对同一资源只保留一档动作，再次授权即改档。"""
        self._require(actor_id, workspace_id, Action.EDIT)
        sub = Subject.parse(subject)
        obj = grantable(Resource.parse(resource, types=self.resource_types), self._hierarchy.resource_types)
        act = parse_action(action)
        async with self._tx(workspace_id) as tx:
            await self._require_subject(tx, sub)
            existing = await store.permissions_of(tx.session, workspace_id, sub.key, obj.key)
            target = R.permission(sub.key, workspace_id, obj.key, act)
            stale = tuple(rule for rule in existing if rule != target)
            await tx.remove_rules(*stale)
            if target not in existing:
                await tx.add_rules(target)
        return GrantView(sub.key, obj.key, act)

    async def revoke(self, actor_id: str, workspace_id: str, subject: str, resource: str) -> None:
        self._require(actor_id, workspace_id, Action.EDIT)
        sub = Subject.parse(subject)
        obj = Resource.parse(resource, types=self.resource_types)
        async with self._tx(workspace_id) as tx:
            existing = await store.permissions_of(tx.session, workspace_id, sub.key, obj.key)
            if not existing:
                raise NotFound("没有这条授权")
            await tx.remove_rules(*existing)

    async def list_grants(self, actor_id: str, workspace_id: str, *, subject: str | None = None,
                          resource: str | None = None) -> tuple[GrantView, ...]:
        """列出显式授权（不含空间角色的默认权限）。"""
        self._require(actor_id, workspace_id, Action.EDIT)
        sub = Subject.parse(subject).key if subject else None
        obj = Resource.parse(resource, types=self.resource_types).key if resource else None
        defaults = set(R.default_role_policies(workspace_id))
        async with self._read(workspace_id) as session:
            rules = await store.permissions_of(session, workspace_id, sub, obj)
        return tuple(GrantView(r.values[0], r.values[2], r.values[3]) for r in rules if r not in defaults)

    async def set_users(self, actor_id: str, workspace_id: str, resource: str,
                        subjects: Sequence[str]) -> tuple[str, ...]:
        """设置一个资源「给谁用」：把这个资源上 use 档的授权整体换成 subjects（每个授 use）。

        技能、MCP 服务在自己的设置里改「给谁用」都走这里，不用去资源授权页一条条加减。edit、owner 档的授权不动，
        已经有这两档的主体也不降成 use；资源授权页仍可以单独改档。返回现在 use 档的主体。"""
        self._require(actor_id, workspace_id, Action.EDIT)
        obj = grantable(Resource.parse(resource, types=self.resource_types), self._hierarchy.resource_types)
        wanted = tuple(dict.fromkeys(Subject.parse(subject) for subject in subjects))
        if len(wanted) > MAX_USERS:
            raise InvalidInput(f"一次最多选 {MAX_USERS} 个对象")
        async with self._tx(workspace_id) as tx:
            for sub in wanted:
                await self._require_subject(tx, sub)
            defaults = set(R.default_role_policies(workspace_id))
            rules = [r for r in await store.permissions_of(tx.session, workspace_id, None, obj.key) if r not in defaults]
            current = {r.values[0]: r for r in rules if r.values[3] == Action.USE}
            higher = {r.values[0] for r in rules if r.values[3] != Action.USE}
            keys = {sub.key for sub in wanted}
            await tx.remove_rules(*(rule for key, rule in current.items() if key not in keys))
            await tx.add_rules(*(R.permission(key, workspace_id, obj.key, Action.USE) for key in sorted(keys)
                                 if key not in current and key not in higher))
        return tuple(sorted(key for key in keys if key not in higher))

    async def seed_users(self, session: AsyncSession, workspace_id: str, resource: str,
                         subjects: Sequence[str]) -> RuleChange:
        """新建资源时在调用方的事务里给默认的「给谁用」（例如新空间技能默认全体成员，授 role:editor use）。
        同 purge_grants：调用方提交后把返回值交给 settle，回滚了就丢掉。"""
        obj = grantable(Resource.parse(resource, types=self.resource_types), self._hierarchy.resource_types)
        rules = tuple(R.permission(Subject.parse(subject).key, workspace_id, obj.key, Action.USE) for subject in subjects)
        await store.insert_rules(session, rules)
        return RuleChange(added=rules)

    async def purge_grants(self, session: AsyncSession, workspace_id: str, *, resources: tuple[str, ...] = (),
                           prefixes: tuple[str, ...] = ()) -> RuleChange:
        """删除资源时，在调用方的事务里删掉指向它的授权。

        session 由调用方开启并已声明工作空间（行级安全）；提交成功后调用方再把返回值交给 settle，
        回滚了就丢掉它，判定器不受影响。prefixes 按前缀匹配资源，例如 `knowledge_base:<知识源id>.`
        同时覆盖这个来源下的单个知识库与 `<知识源id>.*`。空间角色的默认规则不在删除范围内。
        """
        found = await store.permissions_on(session, workspace_id, resources, prefixes)
        defaults = set(R.default_role_policies(workspace_id))
        stale = tuple(rule for rule in found if rule not in defaults)
        await store.delete_rules(session, stale)
        return RuleChange(removed=stale)

    async def settle(self, change: RuleChange) -> None:
        """把调用方事务里已提交的规则变更回放到判定器。"""
        await self._after_commit(change)

    async def _require_subject(self, tx: Tx, sub: Subject) -> None:
        if sub.kind == "user":
            try:
                await self._member(tx.session, tx.workspace_id, sub.id)
            except NotFound as error:
                raise NotFound("该用户不是本空间成员") from error
        elif sub.kind in ("group", "dept"):
            model = UserGroup if sub.kind == "group" else Department
            found = await tx.session.scalar(select(model.id).where(model.id == sub.id,
                                                                   model.workspace_id == tx.workspace_id))
            if found is None:
                raise NotFound("授权主体不存在")
