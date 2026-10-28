"""没绑定工作空间的 API Key 绑到默认空间

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-28 10:00:00

v0.0.2 建 Key 时可以不勾「绑定工作空间」，那时只有默认空间，不绑的 Key 用起来就是默认空间的。后来接口按空间走
（对话在 /api/workspaces/{ws}/ 下），对外知识接口与 MCP 的路径里没有空间、只能从 Key 上取，不绑的 Key 直接被拒；
新建的 Key 也一律绑定当前空间。升级上来的老 Key 绑到默认空间，用法和原来一样。
api_key 是身份模块的全局表，没开行级安全，直接改。
回退不做：分不清哪些 Key 原来没绑。
"""

from collections.abc import Sequence

from alembic import op

revision: str = '0029'
down_revision: str | None = '0028'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE api_key SET workspace_id = 'default' WHERE workspace_id IS NULL")


def downgrade() -> None:
    pass
