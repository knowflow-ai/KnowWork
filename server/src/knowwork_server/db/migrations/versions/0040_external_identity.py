"""外部身份：OIDC 绑定推广成通用的外部身份表，用户邮箱可空，记下谁停用的

Revision ID: 0040
Revises: 0039
Create Date: 2026-10-31 18:00:00

为组织目录同步与更多登录方式（OAuth2、域账号、企业微信等）打底：
- external_identity：「身份域 + 外部 ID → 本地用户」，一个人可以有多条；oidc_identity 的行原样搬进来
  （身份域 = 发行方地址，外部 ID = sub），然后删掉旧表；
- oidc_login_state 改名 login_state，加 method 记下是哪种登录方式发的 state。进行中的登录状态只活 10 分钟，
  升级时直接清掉（正在登录的人重新点一次即可），省得给旧行编一个 method；
- user.email 改为可空：企业微信等目录来的人多半拿不到邮箱，不编假邮箱。有值时仍唯一；
- user.disabled_by：manual 管理员停用，directory 组织同步判定离职停用（重新在职时可以自动恢复）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0040'
down_revision: str | None = '0039'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('external_identity',
    sa.Column('realm', sa.String(length=512), nullable=False),
    sa.Column('subject', sa.String(length=255), nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('source_id', sa.String(length=64), nullable=True),
    sa.Column('account', sa.String(length=255), nullable=True),
    sa.Column('aliases', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('attributes', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('synced_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('realm', 'subject')
    )
    op.create_index(op.f('ix_external_identity_user_id'), 'external_identity', ['user_id'], unique=False)
    op.create_index(op.f('ix_external_identity_source_id'), 'external_identity', ['source_id'], unique=False)
    op.execute("INSERT INTO external_identity (realm, subject, user_id, created_at)"
               " SELECT issuer, subject, user_id, created_at FROM oidc_identity")
    op.drop_index(op.f('ix_oidc_identity_user_id'), table_name='oidc_identity')
    op.drop_table('oidc_identity')

    op.execute("DELETE FROM oidc_login_state")
    op.rename_table('oidc_login_state', 'login_state')
    op.execute('ALTER TABLE login_state RENAME CONSTRAINT oidc_login_state_pkey TO login_state_pkey')
    op.add_column('login_state', sa.Column('method', sa.String(length=64), nullable=False))

    op.alter_column('user', 'email', existing_type=sa.String(length=320), nullable=True)
    op.add_column('user', sa.Column('disabled_by', sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column('user', 'disabled_by')
    # 回退前要先给没有邮箱的账号补上邮箱，否则改回非空会失败（宁可失败也不替人编邮箱）
    op.alter_column('user', 'email', existing_type=sa.String(length=320), nullable=False)

    op.drop_column('login_state', 'method')
    op.execute('ALTER TABLE login_state RENAME CONSTRAINT login_state_pkey TO oidc_login_state_pkey')
    op.rename_table('login_state', 'oidc_login_state')

    op.create_table('oidc_identity',
    sa.Column('issuer', sa.String(length=512), nullable=False),
    sa.Column('subject', sa.String(length=255), nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('issuer', 'subject')
    )
    op.create_index(op.f('ix_oidc_identity_user_id'), 'oidc_identity', ['user_id'], unique=False)
    # 只有 OIDC 的身份域是网址；其他登录方式的身份回退后没处放，丢弃
    op.execute("INSERT INTO oidc_identity (issuer, subject, user_id, created_at)"
               " SELECT realm, subject, user_id, created_at FROM external_identity"
               " WHERE realm LIKE 'http://%' OR realm LIKE 'https://%'")
    op.drop_index(op.f('ix_external_identity_source_id'), table_name='external_identity')
    op.drop_index(op.f('ix_external_identity_user_id'), table_name='external_identity')
    op.drop_table('external_identity')
