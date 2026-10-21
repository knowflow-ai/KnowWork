"""产物预览：记下转换出的 PDF

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-20 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0010'
down_revision: str | None = '0009'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('artifact', sa.Column('preview_digest', sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column('artifact', 'preview_digest')
