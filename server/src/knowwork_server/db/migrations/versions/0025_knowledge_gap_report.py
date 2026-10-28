"""智能体报的知识缺口

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-26 12:00:00

检索后知识库里找不到用户要的内容（或其中一部分）时，智能体调 knowledge_gap 记下缺什么；回合结束时并进每轮汇总的
知识事实。智能体答不上来时常常还会引用沾边的背景，光看「一条都没引用」会漏掉，判断交给作答的智能体自己。
按工作空间做行级安全。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0025'
down_revision: str | None = '0024'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 行级安全的 DDL 写死在迁移里：以后改应用代码不会改变历史迁移的重放结果。
RLS_STATEMENTS = (
    'ALTER TABLE "knowledge_gap_report" ENABLE ROW LEVEL SECURITY',
    'ALTER TABLE "knowledge_gap_report" FORCE ROW LEVEL SECURITY',
    'DROP POLICY IF EXISTS workspace_isolation ON "knowledge_gap_report"',
    'CREATE POLICY workspace_isolation ON "knowledge_gap_report" USING (workspace_id = current_setting(\'app.workspace_id\', true)) WITH CHECK (workspace_id = current_setting(\'app.workspace_id\', true))',
)


def upgrade() -> None:
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
        op.execute(statement)


def downgrade() -> None:
    op.drop_table('knowledge_gap_report')
