"""企业组织：目录来源（AD、企业微信……）同步来的部门树与成员，全系统一份；空间成员规则。

- replace_org：系统操作（组织同步任务调），用一份快照整份替换某个来源的部门与成员。按（来源, 外部 ID）认部门：
  改名、移动保留 id 与授权；快照里没有的软删，回来了恢复。组织表、各空间的成员规则结果与权限规则在同一个事务里改完，
  提交后别的进程（业务服务）收到通知重新加载。快照有问题（外部 ID 重复、成环、太深、成员挂在不存在的部门）整份拒收；
- 成员规则：空间管理员设「某企业部门（可含下级）→ 角色」，命中的人自动是本空间成员（origin=rule），离开部门时移出；
  手工成员不受影响；一人命中多条取最高角色；
- 只放在职的人：哪些人算在职、停用账号由同步任务按来源判，这里只认快照里给的成员。
"""

import secrets
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from . import org_links
from . import rules as R
from .core import Tx
from .errors import Conflict, NotFound
from .groups import GroupsMixin
from .members import BY_RULE
from .models import OrgDepartment, OrgMember, Workspace, WorkspaceMember, WorkspaceMemberRule
from .vocab import Action, InvalidInput, Role, parse_role, validate_id

# 企业组织最多几层：权限的角色链上限（engine.MAX_ROLE_CHAIN = 32）减去用户到部门一层，再留一层余量
MAX_ORG_DEPTH = 30
RULE_ROLES = (Role.EDITOR, Role.ADMIN)  # 成员规则只给成员或管理员；所有者由现任所有者任命
_RANK = {Role.EDITOR: 1, Role.ADMIN: 2}
ACTIVE, REMOVED = "active", "removed"


@dataclass(frozen=True)
class OrgDepartmentInput:
    external_id: str
    parent_external_id: str | None
    name: str
    sort: int = 0


@dataclass(frozen=True)
class OrgMembershipInput:
    user_id: str
    department_external_id: str
    is_primary: bool = False


@dataclass(frozen=True)
class OrgChange:
    added: int = 0
    renamed: int = 0
    moved: int = 0
    removed: int = 0
    restored: int = 0
    memberships_added: int = 0
    memberships_removed: int = 0


@dataclass(frozen=True)
class OrgSourceState:
    """某个来源现在在用的部门：同步前据此算这次要移除几个（误删保护），首次同步后据此给默认空间加规则。"""

    external_ids: frozenset[str]
    roots: tuple[str, ...]  # 顶层部门的 id


@dataclass(frozen=True)
class OrgDepartmentView:
    id: str
    source_id: str
    external_id: str
    parent_id: str | None
    name: str
    sort: int
    member_count: int


@dataclass(frozen=True)
class OrgMemberView:
    user_id: str
    is_primary: bool
    role: str | None   # 在本空间的角色；不在本空间为空


@dataclass(frozen=True)
class MemberRuleView:
    id: str
    department_id: str
    department_name: str
    source_id: str
    department_active: bool
    include_children: bool
    role: str
    created_at: datetime | None


def _check_snapshot(departments: Sequence[OrgDepartmentInput],
                    members: Sequence[OrgMembershipInput]) -> dict[str, OrgDepartmentInput]:
    by_id: dict[str, OrgDepartmentInput] = {}
    for dept in departments:
        if not dept.external_id or len(dept.external_id) > 255:
            raise InvalidInput("部门的外部 ID 为空或过长")
        if dept.external_id in by_id:
            raise InvalidInput(f"部门的外部 ID 重复：{dept.external_id}")
        if not dept.name.strip():
            raise InvalidInput(f"部门 {dept.external_id} 没有名称")
        by_id[dept.external_id] = dept
    depth: dict[str, int] = {}

    def depth_of(external_id: str) -> int:
        """父部门不在快照里（例如企业微信可见范围外）的当顶层。"""
        trail: list[str] = []
        current: str | None = external_id
        while current is not None and current in by_id and current not in depth:
            if current in trail:
                raise InvalidInput(f"部门的上下级成环：{current}")
            trail.append(current)
            current = by_id[current].parent_external_id
        base = depth.get(current, 0) if current is not None else 0
        for offset, node in enumerate(reversed(trail), start=1):
            depth[node] = base + offset
        return depth[external_id]

    for external_id in by_id:
        if depth_of(external_id) > MAX_ORG_DEPTH:
            raise InvalidInput(f"部门层级超过 {MAX_ORG_DEPTH} 层")
    for member in members:
        validate_id(member.user_id, "user_id")
        if member.department_external_id not in by_id:
            raise InvalidInput(f"成员挂在不存在的部门上：{member.department_external_id}")
    return by_id


class EnterpriseMixin(GroupsMixin):
    # ---- 组织同步（系统操作，不判调用者）----

    async def replace_org(self, source_id: str, departments: Sequence[OrgDepartmentInput],
                          members: Sequence[OrgMembershipInput], *, keep_missing_departments: bool = False,
                          keep_memberships_of: Collection[str] = ()) -> OrgChange:
        """keep_missing_departments：快照里没有的部门先不移除（快照不完整、移除太多被保护挂起时）；
        keep_memberships_of：这些人保留现有的部门关系（判了离职、但停用被保护挂起的人，进空间与授权照旧）。"""
        validate_id(source_id, "source_id")
        by_external = _check_snapshot(departments, members)
        async with self._tx_all() as multi:
            session = multi.session
            rows = {row.external_id: row for row in (await session.scalars(
                select(OrgDepartment).where(OrgDepartment.source_id == source_id).with_for_update())).all()}
            counts = {"added": 0, "renamed": 0, "moved": 0, "removed": 0, "restored": 0}
            created: set[str] = set()
            changed: set[str] = set()
            for external_id, dept in by_external.items():
                name = dept.name.strip()[:200]
                row = rows.get(external_id)
                if row is None:
                    row = OrgDepartment(id=f"od_{secrets.token_hex(8)}", source_id=source_id,
                                        external_id=external_id, name=name, sort=dept.sort, status=ACTIVE)
                    session.add(row)
                    rows[external_id] = row
                    created.add(external_id)
                    counts["added"] += 1
                    continue
                if row.status != ACTIVE:
                    row.status = ACTIVE
                    counts["restored"] += 1
                    changed.add(external_id)
                if row.name != name:
                    row.name = name
                    counts["renamed"] += 1
                    changed.add(external_id)
                if row.sort != dept.sort:
                    row.sort = dept.sort
                    changed.add(external_id)
            await session.flush()  # 新部门先有 id，下面才好挂上级
            for external_id, row in rows.items():
                dept = by_external.get(external_id)
                if dept is None:
                    if keep_missing_departments:
                        continue
                    if row.status == ACTIVE:
                        row.status = REMOVED
                        counts["removed"] += 1
                        changed.add(external_id)
                    continue
                parent = rows.get(dept.parent_external_id) if dept.parent_external_id in by_external else None
                parent_id = parent.id if parent is not None else None
                if row.parent_id != parent_id:
                    row.parent_id = parent_id
                    if external_id not in created:
                        counts["moved"] += 1
                        changed.add(external_id)
                if external_id in changed:
                    row.updated_at = func.now()
            added, removed = await self._replace_members(session, source_id, rows, members, set(keep_memberships_of))
            await session.flush()
            for workspace_id in await self._open_workspace_ids(session):
                tx = await multi.workspace(workspace_id)
                await self._apply_member_rules(tx)
                await org_links.sync_workspace(tx)
        return OrgChange(**counts, memberships_added=added, memberships_removed=removed)

    async def _replace_members(self, session, source_id: str, rows: dict[str, OrgDepartment],
                               members: Sequence[OrgMembershipInput], keep: set[str]) -> tuple[int, int]:
        wanted: dict[tuple[str, str], bool] = {}
        for member in members:
            key = (rows[member.department_external_id].id, member.user_id)
            wanted[key] = wanted.get(key, False) or member.is_primary
        source_departments = select(OrgDepartment.id).where(OrgDepartment.source_id == source_id)
        current = {(row.department_id, row.user_id): row for row in (await session.scalars(
            select(OrgMember).where(OrgMember.department_id.in_(source_departments)))).all()}
        stale = [key for key in current if key not in wanted and key[1] not in keep]
        for department_id, user_id in stale:
            await session.execute(delete(OrgMember).where(OrgMember.department_id == department_id,
                                                          OrgMember.user_id == user_id))
        for key, primary in wanted.items():
            row = current.get(key)
            if row is None:
                session.add(OrgMember(department_id=key[0], user_id=key[1], is_primary=primary))
            elif row.is_primary != primary:
                row.is_primary = primary
        return len([key for key in wanted if key not in current]), len(stale)

    async def org_source_state(self, source_id: str) -> OrgSourceState:
        """系统操作（组织同步任务用）：某个来源现在在用的部门。"""
        async with self._sessionmaker() as session:
            rows = (await session.execute(select(OrgDepartment.id, OrgDepartment.external_id, OrgDepartment.parent_id)
                                          .where(OrgDepartment.source_id == source_id,
                                                 OrgDepartment.status == ACTIVE)
                                          .order_by(OrgDepartment.sort, OrgDepartment.name))).all()
        ids = {row.id for row in rows}
        return OrgSourceState(external_ids=frozenset(row.external_id for row in rows),
                              roots=tuple(row.id for row in rows if row.parent_id not in ids))

    async def ensure_member_rule(self, workspace_id: str, department_id: str, *, include_children: bool,
                                 role: str) -> bool:
        """系统操作：本空间还没有这个部门的规则就加一条（第一次接入目录时给默认空间加「整个企业组织 → 成员」）。
        返回是否新加了。管理员删掉以后不会被它加回来：调用方只在第一次同步后调一次。"""
        new_role = parse_role(role)
        if new_role not in RULE_ROLES:
            raise InvalidInput("成员规则只能给成员或管理员")
        async with self._tx(workspace_id) as tx:
            exists = await tx.session.scalar(select(WorkspaceMemberRule.id).where(
                WorkspaceMemberRule.workspace_id == workspace_id, WorkspaceMemberRule.department_id == department_id))
            if exists is not None:
                return False
            dept = await tx.session.get(OrgDepartment, department_id)
            if dept is None or dept.status != ACTIVE:
                raise NotFound("企业部门不存在")
            tx.session.add(WorkspaceMemberRule(id=f"mr_{secrets.token_hex(8)}", workspace_id=workspace_id,
                                               department_id=department_id, include_children=include_children,
                                               role=new_role))
            await tx.session.flush()
            await self._apply_member_rules(tx)
            await org_links.sync_workspace(tx)
        return True

    async def _open_workspace_ids(self, session) -> list[str]:
        return list((await session.scalars(
            select(Workspace.id).where(Workspace.deleted_at.is_(None)).order_by(Workspace.id))).all())

    # ---- 选人用的部门树 ----

    async def org_departments(self, actor_id: str, workspace_id: str) -> tuple[OrgDepartmentView, ...]:
        """全部在用的企业部门（各来源），给授权、成员规则选部门；空间成员都能看（同空间的部门）。"""
        self._require(actor_id, workspace_id, Action.USE)
        async with self._read(workspace_id) as session:
            counts = dict((await session.execute(
                select(OrgMember.department_id, func.count()).group_by(OrgMember.department_id))).all())
            rows = (await session.scalars(select(OrgDepartment).where(OrgDepartment.status == ACTIVE)
                                          .order_by(OrgDepartment.source_id, OrgDepartment.sort,
                                                    OrgDepartment.name))).all()
        return tuple(OrgDepartmentView(id=row.id, source_id=row.source_id, external_id=row.external_id,
                                       parent_id=row.parent_id, name=row.name, sort=row.sort,
                                       member_count=counts.get(row.id, 0)) for row in rows)

    async def org_department_members(self, actor_id: str, workspace_id: str,
                                     department_id: str) -> tuple[OrgMemberView, ...]:
        """组织架构里一个部门（只这一级）的人，给「部门与用户组」页只读查看，标出谁在本空间、什么角色。
        组织架构是全公司的，只给本空间管理员看；只给 ID（姓名由接口层补），不给邮箱。"""
        self._require(actor_id, workspace_id, Action.EDIT)
        async with self._read(workspace_id) as session:
            department = await session.get(OrgDepartment, department_id)
            if department is None or department.status != ACTIVE:
                raise NotFound("组织架构里没有这个部门")
            rows = (await session.execute(
                select(OrgMember.user_id, OrgMember.is_primary, WorkspaceMember.role)
                .outerjoin(WorkspaceMember, (WorkspaceMember.user_id == OrgMember.user_id)
                           & (WorkspaceMember.workspace_id == workspace_id))
                .where(OrgMember.department_id == department_id).order_by(OrgMember.user_id))).all()
        return tuple(OrgMemberView(user_id=user_id, is_primary=primary, role=role) for user_id, primary, role in rows)

    # ---- 空间成员规则 ----

    async def member_rules(self, actor_id: str, workspace_id: str) -> tuple[MemberRuleView, ...]:
        self._require(actor_id, workspace_id, Action.EDIT)
        async with self._read(workspace_id) as session:
            rows = (await session.execute(
                select(WorkspaceMemberRule, OrgDepartment)
                .join(OrgDepartment, OrgDepartment.id == WorkspaceMemberRule.department_id)
                .where(WorkspaceMemberRule.workspace_id == workspace_id)
                .order_by(WorkspaceMemberRule.created_at, WorkspaceMemberRule.id))).all()
        return tuple(_rule_view(rule, dept) for rule, dept in rows)

    async def add_member_rule(self, actor_id: str, workspace_id: str, department_id: str, *,
                              include_children: bool, role: str) -> MemberRuleView:
        self._require(actor_id, workspace_id, Action.EDIT)
        validate_id(department_id, "department_id")
        new_role = parse_role(role)
        if new_role not in RULE_ROLES:
            raise InvalidInput("成员规则只能给成员或管理员")
        try:
            async with self._tx(workspace_id) as tx:
                dept = await tx.session.get(OrgDepartment, department_id)
                if dept is None or dept.status != ACTIVE:
                    raise NotFound("企业部门不存在")
                rule = WorkspaceMemberRule(id=f"mr_{secrets.token_hex(8)}", workspace_id=workspace_id,
                                           department_id=department_id, include_children=include_children,
                                           role=new_role)
                tx.session.add(rule)
                await tx.session.flush()
                await self._apply_member_rules(tx)
                await org_links.sync_workspace(tx)  # 新进来的人带上他们的企业部门
                view = _rule_view(rule, dept)
        except IntegrityError as error:
            raise Conflict("这个部门已经有一条成员规则") from error
        return view

    async def remove_member_rule(self, actor_id: str, workspace_id: str, rule_id: str) -> None:
        """删掉规则：只靠它进来的人随之移出（手工成员不动）。"""
        self._require(actor_id, workspace_id, Action.EDIT)
        async with self._tx(workspace_id) as tx:
            rule = await tx.session.get(WorkspaceMemberRule, rule_id)
            if rule is None or rule.workspace_id != workspace_id:
                raise NotFound("成员规则不存在")
            await tx.session.delete(rule)
            await tx.session.flush()
            await self._apply_member_rules(tx)

    async def _apply_member_rules(self, tx: Tx) -> None:
        """按本空间的成员规则加减规则成员：命中的加进来（或改到应有的角色），不再命中的移出；手工成员不动。"""
        wanted = await self._rule_targets(tx)
        members = {row.user_id: row for row in (await tx.session.scalars(
            select(WorkspaceMember).where(WorkspaceMember.workspace_id == tx.workspace_id)
            .with_for_update())).all()}
        await self._add_member_rows(tx, {u: role for u, role in wanted.items() if u not in members}, BY_RULE)
        for user_id, role in sorted(wanted.items()):
            member = members.get(user_id)
            if member is not None and member.origin == BY_RULE and member.role != role:
                await tx.remove_rules(R.member_role(user_id, member.role, tx.workspace_id))
                member.role = role
                await tx.add_rules(R.member_role(user_id, role, tx.workspace_id))
        await self._drop_members(tx, sorted(u for u, m in members.items() if m.origin == BY_RULE and u not in wanted))

    async def _rule_targets(self, tx: Tx) -> dict[str, str]:
        rules = (await tx.session.scalars(select(WorkspaceMemberRule).where(
            WorkspaceMemberRule.workspace_id == tx.workspace_id))).all()
        if not rules:
            return {}
        tree = (await tx.session.execute(select(OrgDepartment.id, OrgDepartment.parent_id)
                                         .where(OrgDepartment.status == ACTIVE))).all()
        children: dict[str | None, list[str]] = {}
        for row in tree:
            children.setdefault(row.parent_id, []).append(row.id)
        active = {row.id for row in tree}
        wanted: dict[str, str] = {}
        for rule in rules:
            if rule.department_id not in active:
                continue  # 部门已从目录移除：规则留着、不再加人
            covered = _subtree(rule.department_id, children) if rule.include_children else {rule.department_id}
            users = (await tx.session.scalars(select(OrgMember.user_id).where(
                OrgMember.department_id.in_(covered)))).all()
            for user_id in users:
                if _RANK[rule.role] > _RANK.get(wanted.get(user_id, ""), 0):
                    wanted[user_id] = rule.role
        return wanted


def _subtree(root: str, children: dict[str | None, list[str]]) -> set[str]:
    found, stack = {root}, [root]
    while stack:
        for child in children.get(stack.pop(), ()):
            if child not in found:
                found.add(child)
                stack.append(child)
    return found


def _rule_view(rule: WorkspaceMemberRule, dept: OrgDepartment) -> MemberRuleView:
    return MemberRuleView(id=rule.id, department_id=dept.id, department_name=dept.name, source_id=dept.source_id,
                          department_active=dept.status == ACTIVE, include_children=rule.include_children,
                          role=rule.role, created_at=rule.created_at)
