"""技能：个人技能提交到空间与审核

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-29 18:00:00

skill_submission 记个人技能提交到空间的每一次（包摘要与清单当时的快照、状态、审核意见），
同一个个人技能同时只有一份待审；skill 加 origin_skill_id，记空间技能是从哪个个人技能审核通过复制来的。
按工作空间做行级安全。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0030'
down_revision: str | None = '0029'
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
    op.add_column('skill', sa.Column('origin_skill_id', sa.String(length=64), nullable=True))
    op.create_table(
        'skill_submission',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('workspace_id', sa.String(length=64), nullable=False),
        sa.Column('skill_id', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=64), nullable=False),
        sa.Column('version', sa.String(length=64), nullable=False),
        sa.Column('archive_digest', sa.String(length=64), nullable=False),
        sa.Column('manifest', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('note', sa.Text(), nullable=False),
        sa.Column('submitted_by', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('review_note', sa.Text(), nullable=False),
        sa.Column('reviewed_by', sa.String(length=64), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('target_skill_id', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['skill_id'], ['skill.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspace.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_skill_submission_workspace_id'), 'skill_submission', ['workspace_id'])
    op.create_index('ix_skill_submission_status', 'skill_submission', ['workspace_id', 'status', 'created_at'])
    op.create_index('uq_skill_submission_pending', 'skill_submission', ['skill_id'], unique=True,
                    postgresql_where=sa.text("status = 'pending'"))
    for statement in _rls('skill_submission'):
        op.execute(statement)


def downgrade() -> None:
    op.drop_index('uq_skill_submission_pending', table_name='skill_submission',
                  postgresql_where=sa.text("status = 'pending'"))
    op.drop_index('ix_skill_submission_status', table_name='skill_submission')
    op.drop_index(op.f('ix_skill_submission_workspace_id'), table_name='skill_submission')
    op.drop_table('skill_submission')
    op.drop_column('skill', 'origin_skill_id')
