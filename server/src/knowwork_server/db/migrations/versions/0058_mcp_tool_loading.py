"""MCP 服务的工具按需加载开关

Revision ID: 0058
Revises: 0057
Create Date: 2026-11-24 00:30:00

照 DSH 官方 dsh-mcp-client 每个服务的 toolLoading：eager 是工具随每次请求发给模型（原来的样子，默认）；
on_demand 是工具不常驻请求，系统提示里只列名字和一句说明，模型用 mcp__<服务名>__load_tools 加载要用的。
已有的服务都按 eager，行为不变。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0058'
down_revision: str | None = '0057'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('mcp_server', sa.Column('tool_loading', sa.String(length=16), server_default='eager', nullable=False))


def downgrade() -> None:
    op.drop_column('mcp_server', 'tool_loading')
