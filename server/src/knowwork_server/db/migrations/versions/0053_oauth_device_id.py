"""原生应用登录记下设备号

Revision ID: 0053
Revises: 0052
Create Date: 2026-11-12 20:00:00

应用登录时可以报一个自己生成、长期不变的设备号（identity/oauth.py）：同一台设备重新登录后还是它，扩展据此把在设备上执行的
对话认到那台设备。授权码与登录各存一份；没报的是空串。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0053'
down_revision: str | None = '0052'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ('oauth_grant', 'oauth_code'):
        op.add_column(table, sa.Column('device_id', sa.String(length=64), server_default='', nullable=False))


def downgrade() -> None:
    for table in ('oauth_grant', 'oauth_code'):
        op.drop_column(table, 'device_id')
