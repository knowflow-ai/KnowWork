"""原生应用登录（OAuth 授权码 + PKCE）：已登录的设备与一次性授权码

Revision ID: 0052
Revises: 0051
Create Date: 2026-11-12 10:00:00

桌面端这类应用经系统浏览器登录（RFC 8252），拿访问令牌与刷新令牌（identity/oauth.py）。两张表都不按工作空间分，
和浏览器会话、API Key 一样是全局的身份表，令牌只存摘要。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0052'
down_revision: str | None = '0051'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'oauth_grant',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.Column('client_id', sa.String(length=64), nullable=False),
        sa.Column('device_name', sa.String(length=100), nullable=False),
        sa.Column('access_hash', sa.String(length=64), nullable=False),
        sa.Column('access_expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('refresh_hash', sa.String(length=64), nullable=False),
        sa.Column('previous_refresh_hash', sa.String(length=64), nullable=True),
        sa.Column('refresh_expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('access_hash'),
        sa.UniqueConstraint('refresh_hash'),
    )
    op.create_index(op.f('ix_oauth_grant_user_id'), 'oauth_grant', ['user_id'], unique=False)
    op.create_index(op.f('ix_oauth_grant_previous_refresh_hash'), 'oauth_grant', ['previous_refresh_hash'],
                    unique=False)
    op.create_table(
        'oauth_code',
        sa.Column('code_hash', sa.String(length=64), nullable=False),
        sa.Column('client_id', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.Column('redirect_uri', sa.String(length=300), nullable=False),
        sa.Column('code_challenge', sa.String(length=128), nullable=False),
        sa.Column('device_name', sa.String(length=100), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('grant_id', sa.String(length=32), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('code_hash'),
    )


def downgrade() -> None:
    op.drop_table('oauth_code')
    op.drop_index(op.f('ix_oauth_grant_previous_refresh_hash'), table_name='oauth_grant')
    op.drop_index(op.f('ix_oauth_grant_user_id'), table_name='oauth_grant')
    op.drop_table('oauth_grant')
