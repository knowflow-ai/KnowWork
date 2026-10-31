"""技能：环境变量的值

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-30 09:00:00

技能声明的环境变量由管这一层的人填（公共技能系统管理员、空间技能空间管理员、个人技能本人），值加密存在
skill.env_ciphertext，skill.env_filled 记已填了哪些名字（不含值）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0032'
down_revision: str | None = '0031'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('skill', sa.Column('env_ciphertext', sa.Text(), nullable=True))
    op.add_column('skill', sa.Column('env_filled', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column('skill', 'env_filled')
    op.drop_column('skill', 'env_ciphertext')
