"""对一轮回答的赞或踩

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-26 23:00:00

照 DSH 官方的消息反馈：评分、可选的分类与说明；一轮一条（会话 + run_id），再点撤回就删掉。原来界面上的赞、踩只是页面
状态，刷新就没了；落库以后运营大盘才能找出被点踩的回答。按工作空间做行级安全。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0023'
down_revision: str | None = '0022'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 行级安全的 DDL 写死在迁移里：以后改应用代码不会改变历史迁移的重放结果。
RLS_STATEMENTS = (
    'ALTER TABLE "answer_feedback" ENABLE ROW LEVEL SECURITY',
    'ALTER TABLE "answer_feedback" FORCE ROW LEVEL SECURITY',
    'DROP POLICY IF EXISTS workspace_isolation ON "answer_feedback"',
    'CREATE POLICY workspace_isolation ON "answer_feedback" USING (workspace_id = current_setting(\'app.workspace_id\', true)) WITH CHECK (workspace_id = current_setting(\'app.workspace_id\', true))',
)


def upgrade() -> None:
    op.create_table(
        'answer_feedback',
        sa.Column('conversation_id', sa.String(length=64), nullable=False),
        sa.Column('run_id', sa.String(length=32), nullable=False),
        sa.Column('workspace_id', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.String(length=64), nullable=False),
        sa.Column('rating', sa.String(length=8), nullable=False),
        sa.Column('category', sa.String(length=40), nullable=True),
        sa.Column('note', sa.String(length=2000), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversation.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('conversation_id', 'run_id'),
    )
    op.create_index(op.f('ix_answer_feedback_workspace_id'), 'answer_feedback', ['workspace_id'], unique=False)
    for statement in RLS_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.drop_index(op.f('ix_answer_feedback_workspace_id'), table_name='answer_feedback')
    op.drop_table('answer_feedback')
