"""MCP：每人登录（OAuth）

Revision ID: 0034
Revises: 0033
Create Date: 2026-10-30 20:00:00

mcp_server 加 oauth_client_ciphertext（动态注册拿到的或管理员填的 client，加密存）；
mcp_oauth_pending 是还没走完的授权（state 只存摘要，PKCE 校验串加密存，10 分钟过期、用一次就删），按工作空间做行级安全。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0034'
down_revision: str | None = '0033'
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
    op.add_column('mcp_server', sa.Column('oauth_client_ciphertext', sa.Text(), nullable=True))
    op.create_table(
        'mcp_oauth_pending',
        sa.Column('state_hash', sa.String(length=64), nullable=False),
        sa.Column('workspace_id', sa.String(length=64), nullable=False),
        sa.Column('server_id', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.String(length=64), nullable=False),
        sa.Column('verifier_ciphertext', sa.Text(), nullable=False),
        sa.Column('return_to', sa.String(length=500), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['server_id'], ['mcp_server.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspace.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('state_hash'),
    )
    op.create_index(op.f('ix_mcp_oauth_pending_workspace_id'), 'mcp_oauth_pending', ['workspace_id'])
    for statement in _rls('mcp_oauth_pending'):
        op.execute(statement)


def downgrade() -> None:
    op.drop_index(op.f('ix_mcp_oauth_pending_workspace_id'), table_name='mcp_oauth_pending')
    op.drop_table('mcp_oauth_pending')
    op.drop_column('mcp_server', 'oauth_client_ciphertext')
