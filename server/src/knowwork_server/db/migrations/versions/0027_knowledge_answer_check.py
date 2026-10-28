"""回合结束后判断答上了没有，取代智能体自报的知识缺口

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-27 18:00:00

智能体中途调 knowledge_gap 报的缺口只是它当时的判断：常常第一次没查到就报了，接着换个查法找到了，答上的问题也被
算成没答上；按先后取舍又会误删多部分问题里的缺口。改成回合结束后按最终回答判断（照行业里事后用模型评判回答质量的
做法）：检索过知识库、正常答完的一轮记进 knowledge_answer_check 等着，后台每小时一批交给模型判断，判完就删。
knowledge_gap_report 不再用，删掉（0025 只在未发布的版本里）。按工作空间做行级安全。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0027'
down_revision: str | None = '0026'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 行级安全的 DDL 写死在迁移里：以后改应用代码不会改变历史迁移的重放结果。
RLS_STATEMENTS = (
    'ALTER TABLE "knowledge_answer_check" ENABLE ROW LEVEL SECURITY',
    'ALTER TABLE "knowledge_answer_check" FORCE ROW LEVEL SECURITY',
    'DROP POLICY IF EXISTS workspace_isolation ON "knowledge_answer_check"',
    'CREATE POLICY workspace_isolation ON "knowledge_answer_check" USING (workspace_id = current_setting(\'app.workspace_id\', true)) WITH CHECK (workspace_id = current_setting(\'app.workspace_id\', true))',
)


def upgrade() -> None:
    op.create_table(
        'knowledge_answer_check',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('workspace_id', sa.String(length=64), nullable=False),
        sa.Column('conversation_id', sa.String(length=64), nullable=False),
        sa.Column('turn', sa.Integer(), nullable=False),
        sa.Column('question', sa.String(length=2000), nullable=False),
        sa.Column('answer', sa.Text(), nullable=False),
        sa.Column('tries', sa.Integer(), server_default='0', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('conversation_id', 'turn'),
    )
    op.create_index(op.f('ix_knowledge_answer_check_workspace_id'), 'knowledge_answer_check', ['workspace_id'])
    for statement in RLS_STATEMENTS:
        op.execute(statement)
    op.drop_table('knowledge_gap_report')


def downgrade() -> None:
    op.create_table(
        'knowledge_gap_report',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('workspace_id', sa.String(length=64), nullable=False),
        sa.Column('conversation_id', sa.String(length=64), nullable=False),
        sa.Column('turn', sa.Integer(), nullable=True),
        sa.Column('user_id', sa.String(length=64), nullable=False),
        sa.Column('missing', sa.String(length=200), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_knowledge_gap_report_workspace_id'), 'knowledge_gap_report', ['workspace_id'])
    op.create_index(op.f('ix_knowledge_gap_report_conversation_id'), 'knowledge_gap_report', ['conversation_id'])
    for statement in RLS_STATEMENTS:
        op.execute(statement.replace('knowledge_answer_check', 'knowledge_gap_report'))
    op.drop_table('knowledge_answer_check')
