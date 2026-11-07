"""附件导读

Revision ID: 0047
Revises: 0046
Create Date: 2026-11-06 12:00:00

文字放不进对话的长附件，解析完在后台按段并行摘要、再汇总成全文摘要，存成一份导读放在原件旁边
（attachments/digest_job.py）。记导读的文件名、摘要、状态和做到第几段。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0047'
down_revision: str | None = '0046'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('attachment', sa.Column('digest_name', sa.String(length=200), nullable=True))
    op.add_column('attachment', sa.Column('digest_digest', sa.String(length=64), nullable=True))
    op.add_column('attachment', sa.Column('digest_status', sa.String(length=16), nullable=True))
    op.add_column('attachment', sa.Column('digest_done', sa.Integer(), nullable=True))
    op.add_column('attachment', sa.Column('digest_total', sa.Integer(), nullable=True))


def downgrade() -> None:
    for column in ('digest_total', 'digest_done', 'digest_status', 'digest_digest', 'digest_name'):
        op.drop_column('attachment', column)
