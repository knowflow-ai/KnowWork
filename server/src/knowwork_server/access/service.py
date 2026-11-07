"""AccessService：工作空间、成员、部门、用户组与资源授权的统一入口。

所有写操作都带调用者 actor_id，在服务端判定：成员、组织、授权的管理需要 workspace:edit
（空间 owner 与 admin），涉及所有者角色需要 workspace:owner。
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import rules as R
from . import store
from .core import Tx, notify_policy_change
from .enterprise import ACTIVE as ORG_ACTIVE
from .enterprise import EnterpriseMixin
from .errors import AccessDenied, NotFound
from .models import Department, OrgDepartment, UserGroup
from .rules import RuleChange
from .vocab import Action, InvalidInput, Resource, Subject, grantable, parse_action


@dataclass(frozen=True)
class GrantView:
    subject: str
    resource: str
    action: str


MAX_USERS = 200  # 「给谁用」一次最多选这么多对象
MAX_RESOURCES = 200  # 授权页一次最多选这么多资源
MAX_GRANTS = 2000  # 一次最多授这么多条（对象数 × 资源数）
SHARE_ACTIONS = (Action.USE, Action.EDIT)  # 所有者分享只能给这两档：「管理」（再分享、删除）留给所有者与空间管理员


class AccessService(EnterpriseMixin):
    async def grant(self, actor_id: str, workspace_id: str, subject: str, resource: str, action: str) -> GrantView:
        """授权。同一主体对同一资源只保留一档动作，再次授权即改档。"""
        return (await self.grant_many(actor_id, workspace_id, (subject,), (resource,), action))[0]

    async def grant_many(self, actor_id: str, workspace_id: str, subjects: Sequence[str], resources: Sequence[str],
                         action: str) -> tuple[GrantView, ...]:
        """一次授多条（授权页里同一类对象多选、同一类资源多选）：每个对象对每个资源各授一档，同一个事务，
        要么都授上、要么都不授。每条同 grant：对这个资源只保留一档，再授即改档。"""
        self._require(actor_id, workspace_id, Action.EDIT)
        wanted = tuple(dict.fromkeys(Subject.parse(subject) for subject in subjects))
        if not wanted:
            raise InvalidInput("请选择授权对象")
        if len(wanted) > MAX_USERS:
            raise InvalidInput(f"一次最多选 {MAX_USERS} 个对象")
        types = self.added_types
        objects = tuple(dict.fromkeys(grantable(Resource.parse(resource, types=self.resource_types), types)
                                      for resource in resources))
        if not objects:
            raise InvalidInput("请选择资源")
        if len(objects) > MAX_RESOURCES or len(wanted) * len(objects) > MAX_GRANTS:
            raise InvalidInput(f"一次最多授 {MAX_GRANTS} 条（对象数 × 资源数），请分几次授")
        act = parse_action(action)
        keys = {sub.key for sub in wanted}
        # 空间角色的默认权限（管理员对全部技能「管理」等）不算旧档：给「管理员」授低一档时不能把它删掉降了权
        defaults = set(R.default_role_policies(workspace_id))
        async with self._tx(workspace_id) as tx:
            for sub in wanted:
                await self._require_subject(tx, sub)
            for obj in objects:
                # 一个资源上已有的授权一次取回来，再按对象挑：对象多时不用一人一查
                existing = [rule for rule in await store.permissions_of(tx.session, workspace_id, None, obj.key)
                            if rule.values[0] in keys and rule not in defaults]
                targets = [R.permission(sub.key, workspace_id, obj.key, act) for sub in wanted]
                await tx.remove_rules(*(rule for rule in existing if rule not in targets))
                # 和默认权限一模一样的不再写一条
                await tx.add_rules(*(target for target in targets if target not in existing and target not in defaults))
        return tuple(GrantView(sub.key, obj.key, act) for obj in objects for sub in wanted)

    async def revoke(self, actor_id: str, workspace_id: str, subject: str, resource: str) -> None:
        self._require(actor_id, workspace_id, Action.EDIT)
        sub = Subject.parse(subject)
        obj = Resource.parse(resource, types=self.resource_types)
        # 空间角色的默认权限不是授出去的，撤销时不动：撤掉给「管理员」另授的一档，不能连默认的「管理」一起删
        defaults = set(R.default_role_policies(workspace_id))
        async with self._tx(workspace_id) as tx:
            existing = [rule for rule in await store.permissions_of(tx.session, workspace_id, sub.key, obj.key)
                        if rule not in defaults]
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
        obj = grantable(Resource.parse(resource, types=self.resource_types), self.added_types)
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

    async def set_shares(self, workspace_id: str, resource: str, shares: Mapping[str, str]) -> tuple[GrantView, ...]:
        """所有者分享扩展登记的资源（例如问数的看板）：把这个资源上的授权整体换成 shares（主体 → 使用或编辑）。

        「只有空间管理员能授权」的一个例外：谁算这个资源的所有者由扩展核对，调用前扩展自己判，这里不要求空间管理员。
        为此只收扩展登记的资源类型——内置的技能、工具、知识库（含资源层级引入的）仍然只有空间管理员能授；档只能是
        使用、编辑，换的也只是这两档：管理员在资源授权页给的「管理」不在所有者手里，留着不动。授权记录就是资源授权
        那张表，管理员在资源授权页里照样看得到、撤得掉。"""
        obj = self._shareable(workspace_id, resource)
        wanted = {Subject.parse(subject): parse_action(action) for subject, action in shares.items()}
        if any(act not in SHARE_ACTIONS for act in wanted.values()):
            raise InvalidInput("分享只能给「查看」或「编辑」")
        if len(wanted) > MAX_USERS:
            raise InvalidInput(f"一次最多选 {MAX_USERS} 个对象")
        defaults = set(R.default_role_policies(workspace_id))
        async with self._tx(workspace_id) as tx:
            for sub in wanted:
                await self._require_subject(tx, sub)
            rules = [r for r in await store.permissions_of(tx.session, workspace_id, None, obj.key) if r not in defaults]
            existing = [r for r in rules if r.values[3] in SHARE_ACTIONS]
            # 已经有「管理」的不再加一档：同一主体对同一资源只留一档（同 grant），管理本来就含使用、编辑
            managed = {r.values[0] for r in rules if r.values[3] not in SHARE_ACTIONS}
            kept = {sub: act for sub, act in wanted.items() if sub.key not in managed}
            targets = [R.permission(sub.key, workspace_id, obj.key, act) for sub, act in kept.items()]
            await tx.remove_rules(*(rule for rule in existing if rule not in targets))
            await tx.add_rules(*(target for target in targets if target not in existing))
        return tuple(GrantView(sub.key, obj.key, act) for sub, act in kept.items())

    async def shares(self, workspace_id: str, resource: str) -> tuple[GrantView, ...]:
        """扩展登记的资源现在分享给了谁，只列使用、编辑两档（同 set_shares：能不能看这份名单由扩展判）。"""
        obj = self._shareable(workspace_id, resource)
        defaults = set(R.default_role_policies(workspace_id))
        async with self._read(workspace_id) as session:
            rules = await store.permissions_of(session, workspace_id, None, obj.key)
        return tuple(GrantView(r.values[0], r.values[2], r.values[3]) for r in rules
                     if r not in defaults and r.values[3] in SHARE_ACTIONS)

    def _shareable(self, workspace_id: str, resource: str) -> Resource:
        if not self.is_open(workspace_id):
            raise AccessDenied()
        obj = Resource.parse(resource, types=self.resource_types)
        if obj.type not in self._extension_types:
            raise InvalidInput("这类资源只有空间管理员能授权")
        return obj

    async def seed_users(self, session: AsyncSession, workspace_id: str, resource: str,
                         subjects: Sequence[str]) -> RuleChange:
        """新建资源时在调用方的事务里给默认的「给谁用」（例如新空间技能默认全体成员，授 role:editor use）。
        同 purge_grants：调用方提交后把返回值交给 settle，回滚了就丢掉。"""
        obj = grantable(Resource.parse(resource, types=self.resource_types), self.added_types)
        rules = tuple(R.permission(Subject.parse(subject).key, workspace_id, obj.key, Action.USE) for subject in subjects)
        await store.insert_rules(session, rules)
        if rules:
            await notify_policy_change(session, self._origin)
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
        if stale:
            await notify_policy_change(session, self._origin)  # 删知识库这类任务常在 worker 里跑
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
        elif sub.kind == "org":
            # 企业组织的部门全系统一份；新授权只能给在用的部门（已移除的部门上原有的授权保留、不再匹配到人）
            found = await tx.session.scalar(select(OrgDepartment.id).where(OrgDepartment.id == sub.id,
                                                                           OrgDepartment.status == ORG_ACTIVE))
            if found is None:
                raise NotFound("企业部门不存在")
