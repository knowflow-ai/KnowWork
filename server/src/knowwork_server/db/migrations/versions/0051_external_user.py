"""账号种类多一种 external：网页嵌入的认证访问自动建的外部用户

Revision ID: 0051
Revises: 0050
Create Date: 2026-11-11 10:00:00

接入方系统（例如客户的 BI）认出来的人在 KnowWork 里还没有账号时，按渠道设置自动建一个：没有邮箱、没有密码，
不能登录网页端，只能从那个网页嵌入渠道进来（channels/web_identity.py）。
"""

from collections.abc import Sequence

from alembic import op

revision: str = '0051'
down_revision: str | None = '0050'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint('ck_user_kind', 'user', type_='check')
    op.create_check_constraint('ck_user_kind', 'user', "kind in ('person', 'channel', 'external')")


def downgrade() -> None:
    op.execute("UPDATE \"user\" SET status = 'disabled' WHERE kind = 'external'")
    op.execute("UPDATE \"user\" SET kind = 'person' WHERE kind = 'external'")
    op.drop_constraint('ck_user_kind', 'user', type_='check')
    op.create_check_constraint('ck_user_kind', 'user', "kind in ('person', 'channel')")
