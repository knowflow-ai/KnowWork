"""把企业组织物化进一个空间的权限规则：部门上下级 org:<子> → org:<上级>，以及本空间成员属于哪些企业部门
user:<人> → org:<部门>。不是本空间成员的人不写：企业部门只在他所在的空间里替他生效，判定逻辑不用改。

都按「应该有的」和「现在有的」比出差额再增删，重复调用不出错。组织表是全系统的（不开行级安全）；
规则表按空间开着行级安全，调用方的 Tx 已经声明了这个空间。
"""

from sqlalchemy import select

from . import rules as R
from .core import Tx
from .models import CasbinRule, OrgDepartment, OrgMember, WorkspaceMember
from .rules import ORG_PREFIX, Rule

ACTIVE = "active"


def _rule_of(row: CasbinRule) -> Rule:
    return Rule("g", (row.v0, row.v1, row.v2))


async def _current(tx: Tx, *, user_id: str | None = None) -> set[Rule]:
    """本空间现有的企业组织规则（只看指向 org: 的 g 规则）；给了 user_id 只取这个人的。"""
    stmt = select(CasbinRule).where(CasbinRule.workspace_id == tx.workspace_id, CasbinRule.ptype == "g",
                                    CasbinRule.v1.startswith(ORG_PREFIX, autoescape=True))
    if user_id is not None:
        stmt = stmt.where(CasbinRule.v0 == R.user_key(user_id))
    return {_rule_of(row) for row in (await tx.session.scalars(stmt)).all()}


async def _hierarchy(tx: Tx) -> set[Rule]:
    active = OrgDepartment.status == ACTIVE
    rows = (await tx.session.execute(select(OrgDepartment.id, OrgDepartment.parent_id).where(active))).all()
    ids = {row.id for row in rows}
    return {R.org_parent(row.id, row.parent_id, tx.workspace_id) for row in rows if row.parent_id in ids}


async def _memberships(tx: Tx, user_id: str | None = None) -> set[Rule]:
    stmt = (select(OrgMember.user_id, OrgMember.department_id)
            .join(OrgDepartment, OrgDepartment.id == OrgMember.department_id)
            .where(OrgDepartment.status == ACTIVE))
    if user_id is not None:
        stmt = stmt.where(OrgMember.user_id == user_id)
    else:
        members = select(WorkspaceMember.user_id).where(WorkspaceMember.workspace_id == tx.workspace_id)
        stmt = stmt.where(OrgMember.user_id.in_(members))
    rows = (await tx.session.execute(stmt)).all()
    return {R.org_member(row.user_id, row.department_id, tx.workspace_id) for row in rows}


async def _apply(tx: Tx, wanted: set[Rule], current: set[Rule]) -> None:
    await tx.remove_rules(*sorted(current - wanted, key=lambda rule: rule.values))
    await tx.add_rules(*sorted(wanted - current, key=lambda rule: rule.values))


async def sync_workspace(tx: Tx) -> None:
    """整个空间：组织变了（同步）、新建空间时用。"""
    await _apply(tx, await _hierarchy(tx) | await _memberships(tx), await _current(tx))


async def sync_user(tx: Tx, user_id: str) -> None:
    """一个人：刚加进本空间时补上他的企业部门（移出空间时他的规则整个删掉，不用这里）。"""
    await _apply(tx, await _memberships(tx, user_id), await _current(tx, user_id=user_id))
