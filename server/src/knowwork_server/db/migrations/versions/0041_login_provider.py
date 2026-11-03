"""登录方式：后台配置 OIDC 与通用 OAuth2

Revision ID: 0041
Revises: 0040
Create Date: 2026-10-31 19:00:00

OIDC 原来只能用环境变量配一个；改成系统设置的「登录方式」里加、改、停，可以配多个，另加通用 OAuth2
（没有 id_token、要拿访问令牌调对方用户信息接口的统一认证）。环境变量配的 OIDC 照旧可用，不搬进这张表。
客户端密钥加密存，接口只给末四位。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0041'
down_revision: str | None = '0040'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('login_provider',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('name', sa.String(length=40), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('signup', sa.String(length=16), nullable=False),
    sa.Column('config', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('secret_ciphertext', sa.Text(), nullable=True),
    sa.Column('secret_last4', sa.String(length=4), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("kind in ('oidc', 'oauth2')", name='ck_login_provider_kind'),
    sa.CheckConstraint("signup in ('known_only', 'auto')", name='ck_login_provider_signup'),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('login_provider')
