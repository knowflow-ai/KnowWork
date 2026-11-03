"""文档解析服务的设置换成 MinerU 4.1 的解析档位

Revision ID: 0040
Revises: 0039
Create Date: 2026-11-04 10:00:00

MinerU 4.1 改成了 V1 API，请求里不再有解析模式（backend）、语言（lang）、VLM 推理服务地址（server_url）这些字段，
取而代之的是解析档位（tier）：auto 让 MinerU 自动推荐，flash / basic / standard / advanced 强制指定。
backend 列改名成 tier 并按旧值就近映射（pipeline→basic，vlm、hybrid→standard）；server_url、lang 列删掉，
VLM 服务地址改到 MinerU 侧部署配置，语言自动识别。
降级是有损的：server_url、lang 的数据回不来，tier 映射回的 vlm-http-client 没有服务地址，旧代码会按
「没配 VLM 服务」退回 pipeline 解析（不报错），管理员重新保存一次配置才恢复 VLM 模式。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0040'
down_revision: str | None = '0039'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('document_parser', sa.Column('tier', sa.String(length=16), nullable=False, server_default='auto'))
    op.execute("""UPDATE document_parser SET tier = CASE backend
                  WHEN 'pipeline' THEN 'basic'
                  WHEN 'vlm-http-client' THEN 'standard'
                  WHEN 'hybrid-http-client' THEN 'standard'
                  ELSE 'auto' END""")
    op.drop_column('document_parser', 'backend')
    op.drop_column('document_parser', 'server_url')
    op.drop_column('document_parser', 'lang')


def downgrade() -> None:
    op.add_column('document_parser', sa.Column('server_url', sa.String(length=500), nullable=True))
    op.add_column('document_parser', sa.Column('lang', sa.String(length=16), nullable=False, server_default='ch'))
    op.add_column('document_parser', sa.Column('backend', sa.String(length=32), nullable=False, server_default='auto'))
    op.execute("""UPDATE document_parser SET backend = CASE tier
                  WHEN 'flash' THEN 'pipeline'
                  WHEN 'basic' THEN 'pipeline'
                  WHEN 'standard' THEN 'vlm-http-client'
                  WHEN 'advanced' THEN 'hybrid-http-client'
                  ELSE 'auto' END""")
    op.drop_column('document_parser', 'tier')
