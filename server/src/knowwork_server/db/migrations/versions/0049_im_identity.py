"""IM 机器人认人

Revision ID: 0049
Revises: 0048
Create Date: 2026-11-07 16:00:00

认人的渠道里私聊按提问人本人回答（docs 里的 IM 认人方案）：
- conversation 加 channel_id、source：私聊机器人的会话记在本人名下，「最近」里按平台打来源标记；网页上开的为空；
- channel_stranger：私聊了却没认出来的人（没同步到、离职、不是空间成员），管理页单列。按工作空间做行级安全。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0049'
down_revision: str | None = '0048'
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
    op.add_column('conversation', sa.Column('channel_id', sa.String(length=64), nullable=True))
    op.add_column('conversation', sa.Column('source', sa.String(length=16), server_default='', nullable=False))
    op.create_table(
        'channel_stranger',
        sa.Column('channel_id', sa.String(length=64), nullable=False),
        sa.Column('external_id', sa.String(length=255), nullable=False),
        sa.Column('workspace_id', sa.String(length=64), nullable=False),
        sa.Column('display_name', sa.String(length=100), nullable=False),
        sa.Column('reason', sa.String(length=16), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('last_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['channel_id'], ['channel.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('channel_id', 'external_id'),
    )
    op.create_index(op.f('ix_channel_stranger_workspace_id'), 'channel_stranger', ['workspace_id'])
    for statement in _rls('channel_stranger'):
        op.execute(statement)


def downgrade() -> None:
    op.drop_index(op.f('ix_channel_stranger_workspace_id'), table_name='channel_stranger')
    op.drop_table('channel_stranger')
    op.drop_column('conversation', 'source')
    op.drop_column('conversation', 'channel_id')
