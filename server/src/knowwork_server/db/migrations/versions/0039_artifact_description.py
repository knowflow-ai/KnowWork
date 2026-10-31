"""产物：记下模型交付时写的说明

Revision ID: 0039
Revises: 0038
Create Date: 2026-10-31 09:00:00

artifact 加 description：模型用官方 present 交付文件时可以给每个文件写一句说明，
回答下面的文件卡片照官方交付卡片显示它（没写就显示文件类型）。原有产物没有说明，留空。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0039'
down_revision: str | None = '0038'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('artifact', sa.Column('description', sa.String(length=300), nullable=True))


def downgrade() -> None:
    op.drop_column('artifact', 'description')
