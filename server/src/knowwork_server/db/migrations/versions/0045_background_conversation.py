"""会话加「后台会话」标记

Revision ID: 0045
Revises: 0044
Create Date: 2026-11-04 10:00:00

扩展在 worker 里替用户发起的对话（conversations/background.py，例如评测逐题发问）不该挤进本人的「最近」和搜索，
但链接要能打开、方便核对 Agent 做了什么，所以只加一个标记，列表与搜索按它过滤。已有的会话都是用户自己发的。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0045'
down_revision: str | None = '0044'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('conversation', sa.Column('background', sa.Boolean(), server_default='false', nullable=False))


def downgrade() -> None:
    op.drop_column('conversation', 'background')
