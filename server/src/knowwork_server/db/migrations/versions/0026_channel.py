"""渠道：IM 机器人与网页分享

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-27 10:00:00

企业微信、飞书、钉钉机器人与网页分享这类对外对话入口。channel 是入口本身（凭据加密存）；
channel_session 记渠道里的对话位置当前对应哪个会话。按工作空间做行级安全。
user 加 kind：渠道账号（机器人回答时用的身份）与人分开。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0026'
down_revision: str | None = '0025'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("channel", "channel_session")


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
    now = sa.text('now()')
    op.create_table(
        'channel',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('workspace_id', sa.String(length=64), nullable=False),
        sa.Column('platform', sa.String(length=16), nullable=False),
        sa.Column('audience', sa.String(length=16), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('settings', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('public_config', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('secret', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('last_error', sa.String(length=500), nullable=False),
        sa.Column('status_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_channel_workspace_id'), 'channel', ['workspace_id'])
    op.create_table(
        'channel_session',
        sa.Column('channel_id', sa.String(length=64), nullable=False),
        sa.Column('session_key', sa.String(length=300), nullable=False),
        sa.Column('workspace_id', sa.String(length=64), nullable=False),
        sa.Column('conversation_id', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.String(length=64), nullable=False),
        sa.Column('display_name', sa.String(length=100), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.ForeignKeyConstraint(['channel_id'], ['channel.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversation.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('channel_id', 'session_key'),
    )
    op.create_index(op.f('ix_channel_session_workspace_id'), 'channel_session', ['workspace_id'])
    op.create_index(op.f('ix_channel_session_conversation_id'), 'channel_session', ['conversation_id'])
    for table in TABLES:
        for statement in _rls(table):
            op.execute(statement)
    op.add_column('user', sa.Column('kind', sa.String(length=16), server_default='person', nullable=False))
    op.create_check_constraint('ck_user_kind', 'user', "kind in ('person', 'channel')")


def downgrade() -> None:
    op.drop_constraint('ck_user_kind', 'user', type_='check')
    op.drop_column('user', 'kind')
    op.drop_table('channel_session')
    op.drop_table('channel')
