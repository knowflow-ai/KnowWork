"""插件页的标题、说明文档、组件清单

Revision ID: 0057
Revises: 0056
Create Date: 2026-11-22 23:30:00

照 DSH 官方插件页，上传的插件显示作者写的标题、一句话说明、说明文档和组件清单。上传时从包里读出来（语言文件、
README、配置补丁，只读不执行），存在 plugin_installation 上；之前传的没有，界面上退回包名和 package.json 的说明。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0057'
down_revision: str | None = '0056'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('plugin_installation', sa.Column('title', sa.String(length=80), server_default='', nullable=False))
    op.add_column('plugin_installation', sa.Column('detail', sa.Text(), server_default='', nullable=False))
    op.add_column('plugin_installation', sa.Column('components', postgresql.JSONB(astext_type=sa.Text()),
                                                   server_default=sa.text("'[]'::jsonb"), nullable=False))


def downgrade() -> None:
    op.drop_column('plugin_installation', 'components')
    op.drop_column('plugin_installation', 'detail')
    op.drop_column('plugin_installation', 'title')
