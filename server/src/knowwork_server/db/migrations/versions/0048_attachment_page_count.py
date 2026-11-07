"""附件页数

Revision ID: 0048
Revises: 0047
Create Date: 2026-11-06 18:00:00

PDF 解析时记下页数（扫描件也有），交给模型时写明文件共几页、给的就是全部文字（attachments/models.py）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0048'
down_revision: str | None = '0047'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('attachment', sa.Column('page_count', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('attachment', 'page_count')
