"""MCP：企业 MCP（系统管理员登记、发给选定或全部空间），空间里能停用、收窄给谁用

Revision ID: 0037
Revises: 0036
Create Date: 2026-10-31 01:00:00

mcp_server 加 scope（workspace 空间服务 | enterprise 企业服务）与 audience（企业服务发给哪些空间）；企业服务存在默认空间名下，
名字存成 ent_<名字>，和空间服务不撞名，原来的唯一约束不动。mcp_workspace_setting 记空间管理员对到达本空间的企业服务的
设置（停用、收窄），按工作空间做行级安全；企业服务删掉时随外键一起删。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0037'
down_revision: str | None = '0036'
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
    op.add_column('mcp_server', sa.Column('scope', sa.String(length=16), server_default='workspace', nullable=False))
    op.add_column('mcp_server', sa.Column('audience', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.create_table(
        'mcp_workspace_setting',
        sa.Column('workspace_id', sa.String(length=64), nullable=False),
        sa.Column('server_id', sa.String(length=64), nullable=False),
        sa.Column('off', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('narrowed', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('updated_by', sa.String(length=64), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['server_id'], ['mcp_server.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspace.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('workspace_id', 'server_id'),
    )
    for statement in _rls('mcp_workspace_setting'):
        op.execute(statement)


def downgrade() -> None:
    op.drop_table('mcp_workspace_setting')
    op.drop_column('mcp_server', 'audience')
    op.drop_column('mcp_server', 'scope')
