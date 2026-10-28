"""检索记录带上工具调用编号与检索过程，运行详情据此显示每次知识检索是怎么查的

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-27 23:40:00

运行详情里知识检索那一步原来只有交给模型的结果，各路召回（语义、关键词，扩展的视觉检索、多跳检索）、同义词、重排的
情况看不到；这些不能塞给模型（费上下文、干扰作答），改记在检索记录里：call_id 是会话日志里那次工具调用的 callId，
运行详情按它对上；trace 是知识源交回的检索过程。之前的记录两列为空。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0028'
down_revision: str | None = '0027'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('knowledge_search_log', sa.Column('call_id', sa.String(length=200), nullable=True))
    op.add_column('knowledge_search_log',
                  sa.Column('trace', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column('knowledge_search_log', 'trace')
    op.drop_column('knowledge_search_log', 'call_id')
