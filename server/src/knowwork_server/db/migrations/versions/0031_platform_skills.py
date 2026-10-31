"""技能：平台公共技能发给哪些空间，空间停用上层技能

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-29 21:00:00

平台公共技能是 skill 表里 scope = platform 的行（存在默认空间名下），skill 加 audience 记发给哪些空间；
skill_workspace_off 记空间管理员在本空间停用了哪些上层技能（平台公共技能、宿主自带技能），按工作空间做行级安全。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0031'
down_revision: str | None = '0030'
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
    op.add_column('skill', sa.Column('audience', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.create_table(
        'skill_workspace_off',
        sa.Column('workspace_id', sa.String(length=64), nullable=False),
        sa.Column('skill_key', sa.String(length=80), nullable=False),
        sa.Column('disabled_by', sa.String(length=64), nullable=False),
        sa.Column('disabled_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspace.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('workspace_id', 'skill_key'),
    )
    for statement in _rls('skill_workspace_off'):
        op.execute(statement)


def downgrade() -> None:
    op.drop_table('skill_workspace_off')
    op.drop_column('skill', 'audience')
