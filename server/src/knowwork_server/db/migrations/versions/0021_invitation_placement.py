"""邀请记下进哪个空间、当什么角色

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-26 18:00:00

邀请原来不带空间：凭邀请注册的人一律进默认空间当成员。多个空间以后，邀请由某个空间的管理员发，
接受后进那个空间，角色可以是成员或管理员。auth_invitation 是全局表（身份模块不分空间），不开行级安全。
已有的邀请（含还没接受的）记为默认空间、成员，和原来的行为一致；补完默认值就去掉，之后每条邀请都要写明。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0021'
down_revision: str | None = '0020'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('auth_invitation', sa.Column('workspace_id', sa.String(length=64), nullable=False,
                                               server_default='default'))
    op.add_column('auth_invitation', sa.Column('role', sa.String(length=16), nullable=False,
                                               server_default='editor'))
    op.alter_column('auth_invitation', 'workspace_id', server_default=None)
    op.alter_column('auth_invitation', 'role', server_default=None)


def downgrade() -> None:
    op.drop_column('auth_invitation', 'role')
    op.drop_column('auth_invitation', 'workspace_id')
