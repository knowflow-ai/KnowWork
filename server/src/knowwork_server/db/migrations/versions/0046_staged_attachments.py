"""附件可以先传、后挂到对话上

Revision ID: 0046
Revises: 0045
Create Date: 2026-11-06 10:00:00

输入框里一选文件就上传、解析（新对话那时还没有会话），发消息时再认领进对话：conversation_id 允许为空（还没发出去的挂在
user_id 名下）。store_scope 记原件与 Markdown 在对象存储里的命名空间，新附件用自己的 id，认领时文件不用搬；
已有的行为空，照旧用对话 id。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0046'
down_revision: str | None = '0045'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('attachment', sa.Column('store_scope', sa.String(length=64), nullable=True))
    op.alter_column('attachment', 'conversation_id', existing_type=sa.String(length=64), nullable=True)


def downgrade() -> None:
    op.execute("DELETE FROM attachment WHERE conversation_id IS NULL")
    op.alter_column('attachment', 'conversation_id', existing_type=sa.String(length=64), nullable=False)
    op.drop_column('attachment', 'store_scope')
