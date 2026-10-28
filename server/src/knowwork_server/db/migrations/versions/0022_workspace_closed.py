"""工作空间可以关闭（软删除）

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-26 22:00:00

系统管理员删除空间时先做软删除：记下关闭时间，权限规则不再加载、谁都进不去，数据留着，物理清除以后再做。
workspace 表本身不分空间（没有行级安全），直接加列；已有的空间都是没关闭的。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0022'
down_revision: str | None = '0021'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('workspace', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('workspace', 'deleted_at')
