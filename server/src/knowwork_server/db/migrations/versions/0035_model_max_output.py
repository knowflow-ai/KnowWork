"""模型的最大输出

Revision ID: 0035
Revises: 0034
Create Date: 2026-10-30 23:00:00

model 加 max_output_tokens：对话模型单次最多输出多少 token。宿主按全局默认请求 max_tokens（32768），
有的厂商、有的模型上限更小会直接拒绝；填了它，网关转发时把 max_tokens 收紧到这个值。空表示不限，原样转发。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0035'
down_revision: str | None = '0034'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('model', sa.Column('max_output_tokens', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('model', 'max_output_tokens')
