"""部门树。上下级关系写成 g 规则 dept:<子> -> dept:<上级>，成员写成 user -> dept:<部门>。

Casbin 默认角色链最多 10 层，用户 -> 部门占一层，所以部门树深度上限为 8。
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select, update

from . import rules as R
from . import store
from .core import Tx
from .errors import Conflict, NotFound
from .members import MembersMixin
from .models import Department, DepartmentMember
from .vocab import Action, clean_name, validate_id

MAX_DEPARTMENT_DEPTH = 8
_UNCHANGED = object()


@dataclass(frozen=True)
class DepartmentNode:
    id: str
    name: str
    parent_id: str | None
    members: tuple[tuple[str, bool], ...]  # (user_id, is_primary)
    children: tuple["DepartmentNode", ...]


def _depth(parents: dict[str, str | None], dept_id: str | None) -> int:
    depth = 0
    while dept_id is not None:
        depth += 1
        dept_id = parents.get(dept_id)
    return depth


def _subtree_height(parents: dict[str, str | None], root: str) -> int:
    children: dict[str, list[str]] = {}
    for child, parent in parents.items():
        if parent is not None:
            children.setdefault(parent, []).append(child)

    def height(node: str) -> int:
        return 1 + max((height(c) for c in children.get(node, ())), default=0)
    return height(root)


def _is_descendant(parents: dict[str, str | None], candidate: str, ancestor: str) -> bool:
    node: str | None = candidate
    while node is not None:
        if node == ancestor:
            return True
        node = parents.get(node)
    return False


class OrgMixin(MembersMixin):
    async def _parents(self, tx: Tx) -> dict[str, str | None]:
        rows = (await tx.session.execute(select(Department.id, Department.parent_id)
                                         .where(Department.workspace_id == tx.workspace_id)
                                         .with_for_update())).all()
        return {row.id: row.parent_id for row in rows}

    async def _department(self, tx: Tx, dept_id: str) -> Department:
        validate_id(dept_id, "部门 id")
        dept = await tx.session.get(Department, dept_id)
        if dept is None or dept.workspace_id != tx.workspace_id:
            raise NotFound("部门不存在")
        return dept

    async def create_department(self, actor_id: str, workspace_id: str, name: str,
                                parent_id: str | None = None) -> str:
        self._require(actor_id, workspace_id, Action.EDIT)
        name = clean_name(name)
        dept_id = uuid.uuid4().hex
        async with self._tx(workspace_id) as tx:
            parents = await self._parents(tx)
            if parent_id is not None:
                await self._department(tx, parent_id)
                if _depth(parents, parent_id) + 1 > MAX_DEPARTMENT_DEPTH:
                    raise Conflict(f"部门层级不能超过 {MAX_DEPARTMENT_DEPTH} 层")
            tx.session.add(Department(id=dept_id, workspace_id=workspace_id, parent_id=parent_id, name=name))
            await tx.session.flush()
            if parent_id is not None:
                await tx.add_rules(R.dept_parent(dept_id, parent_id, workspace_id))
        return dept_id

    async def update_department(self, actor_id: str, workspace_id: str, dept_id: str, *,
                                name: str | None = None, parent_id=_UNCHANGED) -> None:
        """改名或移动。移动时 parent_id 传 None 表示移到根。"""
        self._require(actor_id, workspace_id, Action.EDIT)
        new_name = clean_name(name) if name is not None else None
        async with self._tx(workspace_id) as tx:
            dept = await self._department(tx, dept_id)
            if new_name is not None:
                dept.name = new_name
            if parent_id is not _UNCHANGED and parent_id != dept.parent_id:
                await self._move(tx, dept, parent_id)

    async def _move(self, tx: Tx, dept: Department, parent_id: str | None) -> None:
        parents = await self._parents(tx)
        if parent_id is not None:
            await self._department(tx, parent_id)
            if _is_descendant(parents, parent_id, dept.id):
                raise Conflict("不能把部门移到它自己或它的下级部门之下")
            if _depth(parents, parent_id) + _subtree_height(parents, dept.id) > MAX_DEPARTMENT_DEPTH:
                raise Conflict(f"部门层级不能超过 {MAX_DEPARTMENT_DEPTH} 层")
        if dept.parent_id is not None:
            await tx.remove_rules(R.dept_parent(dept.id, dept.parent_id, tx.workspace_id))
        dept.parent_id = parent_id
        if parent_id is not None:
            await tx.add_rules(R.dept_parent(dept.id, parent_id, tx.workspace_id))

    async def delete_department(self, actor_id: str, workspace_id: str, dept_id: str) -> None:
        """删除部门：有下级部门时拒绝；成员关系与授给该部门的权限一并删除。"""
        self._require(actor_id, workspace_id, Action.EDIT)
        async with self._tx(workspace_id) as tx:
            dept = await self._department(tx, dept_id)
            if any(parent == dept_id for parent in (await self._parents(tx)).values()):
                raise Conflict("请先删除或移走下级部门")
            await tx.remove_rules(*await store.rules_touching(tx.session, workspace_id, R.dept_key(dept_id)))
            await tx.session.delete(dept)

    async def add_department_member(self, actor_id: str, workspace_id: str, dept_id: str, user_id: str,
                                    is_primary: bool = False) -> None:
        self._require(actor_id, workspace_id, Action.EDIT)
        async with self._tx(workspace_id) as tx:
            await self._department(tx, dept_id)
            await self._require_member(tx, user_id)
            if is_primary:
                await tx.session.execute(update(DepartmentMember).where(
                    DepartmentMember.workspace_id == workspace_id, DepartmentMember.user_id == user_id)
                    .values(is_primary=False))
            existing = await tx.session.get(DepartmentMember, (dept_id, user_id))
            if existing is None:
                tx.session.add(DepartmentMember(department_id=dept_id, user_id=user_id, workspace_id=workspace_id,
                                                is_primary=is_primary))
                await tx.add_rules(R.dept_member(user_id, dept_id, workspace_id))
            else:
                existing.is_primary = is_primary

    async def remove_department_member(self, actor_id: str, workspace_id: str, dept_id: str, user_id: str) -> None:
        self._require(actor_id, workspace_id, Action.EDIT)
        async with self._tx(workspace_id) as tx:
            await self._department(tx, dept_id)
            existing = await tx.session.get(DepartmentMember, (dept_id, validate_id(user_id, "user_id")))
            if existing is None:
                raise NotFound("该用户不在此部门")
            await tx.session.delete(existing)
            await tx.remove_rules(R.dept_member(user_id, dept_id, workspace_id))

    async def department_tree(self, actor_id: str, workspace_id: str) -> tuple[DepartmentNode, ...]:
        self._require(actor_id, workspace_id, Action.USE)
        async with self._read(workspace_id) as session:
            depts = (await session.scalars(select(Department).where(Department.workspace_id == workspace_id)
                                           .order_by(Department.name, Department.id))).all()
            members = (await session.scalars(select(DepartmentMember)
                                             .where(DepartmentMember.workspace_id == workspace_id)
                                             .order_by(DepartmentMember.user_id))).all()
        return _build_tree(depts, members)


def _build_tree(depts, members) -> tuple[DepartmentNode, ...]:
    by_dept: dict[str, list[tuple[str, bool]]] = {}
    for m in members:
        by_dept.setdefault(m.department_id, []).append((m.user_id, m.is_primary))
    children: dict[str | None, list] = {}
    for d in depts:
        children.setdefault(d.parent_id, []).append(d)

    def node(d) -> DepartmentNode:
        return DepartmentNode(id=d.id, name=d.name, parent_id=d.parent_id, members=tuple(by_dept.get(d.id, ())),
                              children=tuple(node(c) for c in children.get(d.id, ())))
    return tuple(node(d) for d in children.get(None, ()))
