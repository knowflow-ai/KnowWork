"""casbin_rule 表的读写，相当于一个极简的 Casbin 适配器。

不用社区的 casbin-async-sqlalchemy-adapter，原因：
1. 它的会话在构造时绑定（或自建会话并自行提交），无法把 g/p 规则的写入放进业务的同一个事务；
2. 它的表没有 workspace_id，策略表无法受行级安全保护；
3. 它的 load_policy 一次读全表，在行级安全下读不到（这里按空间逐个读）。
"""

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .models import CasbinRule, Workspace
from .rls import set_workspace
from .rules import Rule


def _rule_of(row: CasbinRule) -> Rule:
    if row.ptype == "p":
        return Rule("p", (row.v0, row.v1, row.v2, row.v3))
    return Rule("g", (row.v0, row.v1, row.v2))


def _match(rule: Rule):
    v0, v1, v2, v3 = rule.padded()
    return and_(CasbinRule.ptype == rule.ptype, CasbinRule.v0 == v0, CasbinRule.v1 == v1,
                CasbinRule.v2 == v2, CasbinRule.v3 == v3)


async def insert_rules(session: AsyncSession, rules: tuple[Rule, ...]) -> None:
    if not rules:
        return
    rows = []
    for rule in rules:
        v0, v1, v2, v3 = rule.padded()
        rows.append({"workspace_id": rule.workspace_id, "ptype": rule.ptype, "v0": v0, "v1": v1, "v2": v2, "v3": v3})
    await session.execute(insert(CasbinRule).values(rows).on_conflict_do_nothing(constraint="uk_casbin_rule"))


async def delete_rules(session: AsyncSession, rules: tuple[Rule, ...]) -> None:
    if rules:
        await session.execute(delete(CasbinRule).where(or_(*(_match(rule) for rule in rules))))


async def rules_touching(session: AsyncSession, workspace_id: str, subject_key: str) -> tuple[Rule, ...]:
    """某主体在本空间的全部规则：它作为 p 的主体、作为 g 的下级或上级。"""
    stmt = select(CasbinRule).where(
        CasbinRule.workspace_id == workspace_id,
        or_(CasbinRule.v0 == subject_key, and_(CasbinRule.ptype == "g", CasbinRule.v1 == subject_key)),
    )
    return tuple(_rule_of(row) for row in (await session.scalars(stmt)).all())


async def permissions_of(session: AsyncSession, workspace_id: str, subject_key: str | None = None,
                         obj: str | None = None) -> tuple[Rule, ...]:
    stmt = select(CasbinRule).where(CasbinRule.workspace_id == workspace_id, CasbinRule.ptype == "p")
    if subject_key is not None:
        stmt = stmt.where(CasbinRule.v0 == subject_key)
    if obj is not None:
        stmt = stmt.where(CasbinRule.v2 == obj)
    return tuple(_rule_of(row) for row in (await session.scalars(stmt.order_by(CasbinRule.id))).all())


async def permissions_on(session: AsyncSession, workspace_id: str, resources: tuple[str, ...],
                         prefixes: tuple[str, ...]) -> tuple[Rule, ...]:
    """指向这些资源（精确）或资源名以这些前缀开头的 p 规则。前缀里的 _ 与 % 按字面匹配。"""
    conditions = [*((CasbinRule.v2.in_(resources),) if resources else ()),
                  *(CasbinRule.v2.startswith(prefix, autoescape=True) for prefix in prefixes)]
    if not conditions:
        return ()
    stmt = select(CasbinRule).where(CasbinRule.workspace_id == workspace_id, CasbinRule.ptype == "p",
                                    or_(*conditions))
    return tuple(_rule_of(row) for row in (await session.scalars(stmt.order_by(CasbinRule.id))).all())


async def load_all(sessionmaker: async_sessionmaker) -> tuple[Rule, ...]:
    """按空间逐个读取策略：每个空间在自己的事务里设置 app.workspace_id，行级安全下也读得到。"""
    async with sessionmaker() as session:
        workspace_ids = (await session.scalars(select(Workspace.id))).all()
    rules: list[Rule] = []
    for workspace_id in workspace_ids:
        async with sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            rows = (await session.scalars(select(CasbinRule).where(CasbinRule.workspace_id == workspace_id))).all()
            rules.extend(_rule_of(row) for row in rows)
    return tuple(rules)
