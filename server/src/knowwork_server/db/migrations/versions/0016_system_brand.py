"""系统品牌设置表

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-21 10:00:00

品牌（名称、Logo）是整套部署的门面，全局单行，和 system_setup 一样不分工作空间、不开行级安全。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0016'
down_revision: str | None = '0015'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('system_brand',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=40), nullable=True),
    sa.Column('logo', sa.LargeBinary(), nullable=True),
    sa.Column('logo_type', sa.String(length=32), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_by', sa.String(length=64), nullable=True),
    sa.CheckConstraint('id = 1', name='ck_system_brand_singleton'),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('system_brand')
