"""动作回执记下工具交的界面卡片

Revision ID: 0044
Revises: 0043
Create Date: 2026-11-02 23:00:00

工具结果可以另带一份只给界面看的卡片（tools/presentation.py，走 DSH 工具输出的 presentationMeta）。宿主重试同一调用时
回执重放第一次的结果，卡片也得跟着交回，否则界面上这张卡就没了，所以和结果一起存。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0044'
down_revision: str | None = '0043'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('action_receipt', sa.Column('presentation', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column('action_receipt', 'presentation')
