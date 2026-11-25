"""内容安全策略

Revision ID: 0055
Revises: 0054
Create Date: 2026-11-22 12:00:00

管理员给空间配一份敏感词：员工发给模型的内容、模型的回答和它要调用的工具参数，命中就拦下并记审计。
content_safety_policy 每个空间一行，按工作空间做行级安全；没有这一行就是没开。word_sources 是勾了哪几种词表
（custom 是管理员自己填的，其余是扩展登记的现成词表）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0055'
down_revision: str | None = '0054'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _rls(table: str) -> tuple[str, ...]:
    # 行级安全的 DDL 写死在迁移里：以后改应用代码不会改变历史迁移的重放结果。
    condition = "workspace_id = current_setting('app.workspace_id', true)"
    return (
        f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY',
        f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY',
        f'DROP POLICY IF EXISTS workspace_isolation ON "{table}"',
        f'CREATE POLICY workspace_isolation ON "{table}" USING ({condition}) WITH CHECK ({condition})',
    )


def upgrade() -> None:
    op.create_table(
        'content_safety_policy',
        sa.Column('workspace_id', sa.String(length=64), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('input_enabled', sa.Boolean(), nullable=False),
        sa.Column('output_enabled', sa.Boolean(), nullable=False),
        sa.Column('word_sources', postgresql.JSONB(astext_type=sa.Text()),
                  server_default=sa.text('\'["custom"]\'::jsonb'), nullable=False),
        sa.Column('words', sa.Text(), nullable=False),
        sa.Column('block_message', sa.String(length=500), nullable=False),
        sa.Column('updated_by', sa.String(length=64), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('workspace_id'),
    )
    for statement in _rls('content_safety_policy'):
        op.execute(statement)


def downgrade() -> None:
    op.drop_table('content_safety_policy')
