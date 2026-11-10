"""模型：页面上要不要显示思考

Revision ID: 0050
Revises: 0049
Create Date: 2026-11-10 01:00:00

页面边想边显示模型的思考（照 ChatGPT）；有的模型不管怎么要求都用英文想，管理员可以按模型关掉，关了只显示「思考中」。
默认显示。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0050'
down_revision: str | None = '0049'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('model', sa.Column('show_thinking', sa.Boolean(), server_default=sa.true(), nullable=False))


def downgrade() -> None:
    op.drop_column('model', 'show_thinking')
