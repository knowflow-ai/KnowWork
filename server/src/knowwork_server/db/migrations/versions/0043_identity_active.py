"""外部身份记下在来源里是否在职

Revision ID: 0043
Revises: 0042
Create Date: 2026-10-31 22:00:00

目录同步要按来源判离职：不在快照里、被禁用都算不在职。一个人可能在好几个来源里（AD 一条、企业微信一条），
任一来源判离职就停用账号，所有来源都在职才自动恢复，所以要按身份记。原有的（登录建立的）都算在职。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0043'
down_revision: str | None = '0042'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('external_identity', sa.Column('active', sa.Boolean(), server_default=sa.text('true'),
                                                 nullable=False))


def downgrade() -> None:
    op.drop_column('external_identity', 'active')
