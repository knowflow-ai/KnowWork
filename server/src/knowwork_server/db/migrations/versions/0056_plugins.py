"""插件的登记

Revision ID: 0056
Revises: 0055
Create Date: 2026-11-22 22:00:00

管理员给空间定每个插件的档，成员在可选的里面自己开关；每次开会话把这个人生效的插件交给宿主。
- plugin_installation：空间里装了什么（预置插件的档；上传的插件、个人插件用的字段先建好，后面接着用）；
- plugin_switch：一个人对可选插件的开关；
- plugin_workspace_setting：空间的管控开关。
三张都按工作空间做行级安全。
- session_host：一段对话落在哪个宿主（用了上传插件的人有自己的专属宿主）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0056'
down_revision: str | None = '0055'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ('plugin_installation', 'plugin_switch', 'plugin_workspace_setting')


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
        'plugin_installation',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('workspace_id', sa.String(length=64), nullable=False),
        sa.Column('owner_user_id', sa.String(length=64), nullable=False),
        sa.Column('plugin', sa.String(length=128), nullable=False),
        sa.Column('source', sa.String(length=16), nullable=False),
        sa.Column('mode', sa.String(length=16), nullable=False),
        sa.Column('version', sa.String(length=64), nullable=False),
        sa.Column('description', sa.String(length=500), nullable=False),
        sa.Column('archive_bytes', sa.BigInteger(), nullable=False),
        sa.Column('narrowed', sa.Boolean(), nullable=False),
        sa.Column('archive_digest', sa.String(length=64), nullable=True),
        sa.Column('targets', postgresql.JSONB(astext_type=sa.Text()),
                  server_default=sa.text("""'["cloud", "device"]'::jsonb"""), nullable=False),
        sa.Column('capabilities', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"),
                  nullable=False),
        sa.Column('tool_names', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"),
                  nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('status_reason', sa.String(length=500), nullable=False),
        sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_by', sa.String(length=64), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('workspace_id', 'owner_user_id', 'plugin', name='uq_plugin_installation'),
    )
    op.create_index(op.f('ix_plugin_installation_workspace_id'), 'plugin_installation', ['workspace_id'])
    op.create_table(
        'plugin_switch',
        sa.Column('workspace_id', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.String(length=64), nullable=False),
        sa.Column('plugin', sa.String(length=128), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('workspace_id', 'user_id', 'plugin'),
    )
    op.create_table(
        'plugin_workspace_setting',
        sa.Column('workspace_id', sa.String(length=64), nullable=False),
        sa.Column('allow_personal', sa.Boolean(), nullable=False),
        sa.Column('allow_personal_device', sa.Boolean(), nullable=False),
        sa.Column('blocked', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"),
                  nullable=False),
        sa.Column('updated_by', sa.String(length=64), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('workspace_id'),
    )
    for table in TABLES:
        for statement in _rls(table):
            op.execute(statement)
    # 对话落在哪个宿主（共享宿主，或某个人的专属宿主）。只有编号，不带 workspace_id、不做行级安全：
    # 后台任务按对话转发调用时不一定在某个空间的上下文里
    op.create_table(
        'session_host',
        sa.Column('session_id', sa.String(length=128), nullable=False),
        sa.Column('host_workspace', sa.String(length=64), nullable=True),
        sa.Column('host_user', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('session_id'),
    )


def downgrade() -> None:
    op.drop_table('session_host')
    op.drop_table('plugin_workspace_setting')
    op.drop_table('plugin_switch')
    op.drop_index(op.f('ix_plugin_installation_workspace_id'), table_name='plugin_installation')
    op.drop_table('plugin_installation')
