"""MCP：认证方式与每人的连接

Revision ID: 0033
Revises: 0032
Create Date: 2026-10-30 14:00:00

mcp_server 加 auth（shared 共用账号、user_token 每人令牌、oauth 每人登录，旧数据是 shared，行为不变）与 auth_config；
mcp_connection 是每人每个服务一条连接（令牌加密存），按工作空间做行级安全。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0033'
down_revision: str | None = '0032'
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
    op.add_column('mcp_server', sa.Column('auth', sa.String(length=16), server_default='shared', nullable=False))
    op.add_column('mcp_server', sa.Column('auth_config', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.create_table(
        'mcp_connection',
        sa.Column('server_id', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.String(length=64), nullable=False),
        sa.Column('workspace_id', sa.String(length=64), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('secret_ciphertext', sa.Text(), nullable=False),
        sa.Column('display', sa.String(length=100), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('connected_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['server_id'], ['mcp_server.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspace.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('server_id', 'user_id'),
    )
    op.create_index(op.f('ix_mcp_connection_workspace_id'), 'mcp_connection', ['workspace_id'])
    for statement in _rls('mcp_connection'):
        op.execute(statement)


def downgrade() -> None:
    op.drop_index(op.f('ix_mcp_connection_workspace_id'), table_name='mcp_connection')
    op.drop_table('mcp_connection')
    op.drop_column('mcp_server', 'auth_config')
    op.drop_column('mcp_server', 'auth')
