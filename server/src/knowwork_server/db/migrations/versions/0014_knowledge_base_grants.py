"""知识源上的授权改写成知识库授权

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-20 18:10:00

2026-10-20 起「谁能查」只授在知识库上，知识源只表示谁能改连接、由空间角色的默认规则给：
- knowledge_source:<id> 上的显式授权（不论哪一档）改写成 knowledge_base:<id>.* 的 use，即这个来源下的全部知识库；
  以前给过 edit、owner 的人从此不能改这条连接，改连接只看空间角色（管理员默认是全部知识源的 owner）；
- knowledge_source:* 的显式授权改写成 knowledge_base:*；
- 知识源已经删掉的（以前删除知识源不清授权，授权页留下只显示 id 的孤儿授权）直接删掉，不改写；
- 空间角色的默认规则（role:admin 对 knowledge_source:* 的 owner）保留不动。
casbin_rule 开了 FORCE ROW LEVEL SECURITY，迁移账号是 owner 也受约束，所以逐个空间声明 app.workspace_id 再改。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0014'
down_revision: str | None = '0013'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EXPLICIT = """ptype = 'p' AND left(v2, 17) = 'knowledge_source:'
    AND NOT (v0 = 'role:admin' AND v2 = 'knowledge_source:*' AND v3 = 'owner')"""

_UPGRADE = (
    f"""INSERT INTO casbin_rule (workspace_id, ptype, v0, v1, v2, v3)
        SELECT workspace_id, 'p', v0, v1,
               CASE WHEN v2 = 'knowledge_source:*' THEN 'knowledge_base:*'
                    ELSE 'knowledge_base:' || substr(v2, 18) || '.*' END,
               'use'
        FROM casbin_rule
        WHERE {_EXPLICIT}
          AND (v2 = 'knowledge_source:*'
               OR EXISTS (SELECT 1 FROM knowledge_source s WHERE s.id = substr(casbin_rule.v2, 18)))
        ON CONFLICT ON CONSTRAINT uk_casbin_rule DO NOTHING""",
    f"DELETE FROM casbin_rule WHERE {_EXPLICIT}",
)

# 回退只能还原「整个来源」这一种：单个知识库的授权在旧模型里没有对应写法，留着不动（旧代码不认它）
_DOWNGRADE = (
    """INSERT INTO casbin_rule (workspace_id, ptype, v0, v1, v2, v3)
        SELECT workspace_id, 'p', v0, v1, 'knowledge_source:' || substr(v2, 16, length(v2) - 17), v3
        FROM casbin_rule
        WHERE ptype = 'p' AND left(v2, 15) = 'knowledge_base:' AND right(v2, 2) = '.*'
        ON CONFLICT ON CONSTRAINT uk_casbin_rule DO NOTHING""",
    "DELETE FROM casbin_rule WHERE ptype = 'p' AND left(v2, 15) = 'knowledge_base:' AND right(v2, 2) = '.*'",
)


def _each_workspace(statements: tuple[str, ...]) -> None:
    bind = op.get_bind()
    for workspace_id in bind.execute(sa.text("SELECT id FROM workspace")).scalars().all():
        bind.execute(sa.text("SELECT set_config('app.workspace_id', :w, true)"), {"w": workspace_id})
        for statement in statements:
            bind.execute(sa.text(statement))
    bind.execute(sa.text("SELECT set_config('app.workspace_id', '', true)"))  # 同一事务里后面的迁移不受影响


def upgrade() -> None:
    _each_workspace(_UPGRADE)


def downgrade() -> None:
    _each_workspace(_DOWNGRADE)
