"""测试扩展的第一条迁移：单独一条分支，依赖开源主线的 0018。"""

import sqlalchemy as sa
from alembic import op

revision = "demo_0001"
down_revision = None
branch_labels = ("demo",)
depends_on = "0018"


def upgrade() -> None:
    op.create_table("demo_note",
                    sa.Column("id", sa.String(length=64), primary_key=True),
                    sa.Column("workspace_id", sa.String(length=64), nullable=False))


def downgrade() -> None:
    op.drop_table("demo_note")
