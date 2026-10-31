"""MCP：企业 MCP 记下从哪个空间服务收上来的

Revision ID: 0038
Revises: 0037
Create Date: 2026-10-31 03:00:00

mcp_server 加 origin_server_id：从空间 MCP 收为企业 MCP 时记下来源，那个空间的列表据此写明「已收为企业 MCP」，
提醒确认企业 MCP 能用后删掉原来那份。不加外键：来源删了，这条记录留着也无害。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0038'
down_revision: str | None = '0037'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('mcp_server', sa.Column('origin_server_id', sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column('mcp_server', 'origin_server_id')
