"""「只读成员」并进「成员」

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-26 12:00:00

空间角色原来有四档，「只读成员」（viewer）与「成员」（editor）的权限一直一样：两者的默认权限都只有 workspace:use，
代码里没有任何地方区分它们，名字里的「只读」却会让人以为受了限制。合并成「成员」：
- workspace_member.role 的 viewer 改成 editor；
- 角色归属（g 规则 user → role:viewer）改到 role:editor；
- 授给 role:viewer 的权限（p 规则）改到 role:editor；同一个资源两边都有时留更高的一档（use < edit < owner）。
直接授给个人、部门、用户组的权限不动。新装的库没有 viewer，跑这里什么都不改。
workspace_member、casbin_rule 开了 FORCE ROW LEVEL SECURITY，迁移账号不是超级用户，逐个空间声明 app.workspace_id（同 0019）。
回退不做：合并以后分不清谁原来是只读成员。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0020'
down_revision: str | None = '0019'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RANK = {"use": 1, "edit": 2, "owner": 3}

_MEMBERS = "UPDATE workspace_member SET role = 'editor' WHERE role = 'viewer'"
_LINKS = """INSERT INTO casbin_rule (workspace_id, ptype, v0, v1, v2, v3)
    SELECT workspace_id, 'g', v0, 'role:editor', v2, v3 FROM casbin_rule WHERE ptype = 'g' AND v1 = 'role:viewer'
    ON CONFLICT ON CONSTRAINT uk_casbin_rule DO NOTHING"""
_DROP_LINKS = "DELETE FROM casbin_rule WHERE ptype = 'g' AND v1 = 'role:viewer'"
_GRANTS = """SELECT v0, v1, v2, v3 FROM casbin_rule WHERE ptype = 'p' AND v0 IN ('role:viewer', 'role:editor')"""


def _merge_grants(bind: sa.Connection, workspace_id: str) -> None:
    best: dict[tuple[str, str], str] = {}
    for _, domain, resource, action in bind.execute(sa.text(_GRANTS)).all():
        current = best.get((domain, resource))
        if current is None or RANK.get(action, 0) > RANK.get(current, 0):
            best[(domain, resource)] = action
    bind.execute(sa.text("DELETE FROM casbin_rule WHERE ptype = 'p' AND v0 IN ('role:viewer', 'role:editor')"))
    for (domain, resource), action in sorted(best.items()):
        bind.execute(sa.text("INSERT INTO casbin_rule (workspace_id, ptype, v0, v1, v2, v3)"
                             " VALUES (:w, 'p', 'role:editor', :d, :r, :a)"),
                     {"w": workspace_id, "d": domain, "r": resource, "a": action})


def upgrade() -> None:
    bind = op.get_bind()
    for workspace_id in bind.execute(sa.text("SELECT id FROM workspace")).scalars().all():
        bind.execute(sa.text("SELECT set_config('app.workspace_id', :w, true)"), {"w": workspace_id})
        bind.execute(sa.text(_MEMBERS))
        bind.execute(sa.text(_LINKS))
        bind.execute(sa.text(_DROP_LINKS))
        _merge_grants(bind, workspace_id)
    bind.execute(sa.text("SELECT set_config('app.workspace_id', '', true)"))


def downgrade() -> None:
    pass
