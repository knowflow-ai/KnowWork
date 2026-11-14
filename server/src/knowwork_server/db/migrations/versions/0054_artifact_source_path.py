"""产物记下交付时的路径

Revision ID: 0054
Revises: 0053
Create Date: 2026-11-13 10:00:00

对话里的文件按路径读（回答里的文件链接、交付卡片，网页与桌面同一套）：交付过的文件读收进存储的那份，沙箱回收后也在，
所以产物要记下它交付时在沙箱里的路径（/workspace/...）。早先收的没有路径，留空。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0054'
down_revision: str | None = '0053'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('artifact', sa.Column('source_path', sa.String(length=4096), nullable=True))


def downgrade() -> None:
    op.drop_column('artifact', 'source_path')
