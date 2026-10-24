"""从 v0.0.1 升上来的库补齐 0002 发布后的改动

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-23 20:00:00

v0.0.1 发布后，0002 建表迁移被直接改过（当时以为还没有装好的库）：加了 document_parser 表（管理后台「模型 →
文档解析」）与 model.thinking 列（思考开关），去掉了 model_group、model_group_item 两张表（模型分组授权）。
v0.0.1 的库早跑过旧的 0002，不会再跑一遍，这里补齐：
- 没有 document_parser 就建，并开行级安全；model 没有 thinking 列就加；
- 删两张模型分组表，以及 casbin_rule 里 model_group:* 的授权（显式授权和空间角色的默认权限）；
- 运行配置快照去掉 models：RuntimeProfile 不收多余字段（extra=forbid），留着的话老会话一读快照就校验失败，
  知识工具报「暂时读不到本轮的知识范围」。
每一步都先看库里有没有，新装的库（按改过的 0002 建表）跑这里什么都不改。
casbin_rule、runtime_profile 开了 FORCE ROW LEVEL SECURITY，迁移账号不是超级用户，逐个空间声明 app.workspace_id（同 0015）。
回退不做：新装的库本来就有这些，回退删掉反而和 0002 对不上；删掉的分组与授权也找不回来。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0019'
down_revision: str | None = '0018'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 与 0002 里 document_parser 的行级安全一致
_PARSER_RLS = (
    'ALTER TABLE "document_parser" ENABLE ROW LEVEL SECURITY',
    'ALTER TABLE "document_parser" FORCE ROW LEVEL SECURITY',
    'DROP POLICY IF EXISTS workspace_isolation ON "document_parser"',
    ("CREATE POLICY workspace_isolation ON \"document_parser\""
     " USING (workspace_id = current_setting('app.workspace_id', true))"
     " WITH CHECK (workspace_id = current_setting('app.workspace_id', true))"),
)

_DROP_GROUP_GRANTS = "DELETE FROM casbin_rule WHERE ptype = 'p' AND starts_with(v2, 'model_group:')"
_DROP_SNAPSHOT_MODELS = "UPDATE runtime_profile SET snapshot = snapshot - 'models' WHERE snapshot ? 'models'"


def _each_workspace(*statements: str) -> None:
    bind = op.get_bind()
    for workspace_id in bind.execute(sa.text("SELECT id FROM workspace")).scalars().all():
        bind.execute(sa.text("SELECT set_config('app.workspace_id', :w, true)"), {"w": workspace_id})
        for statement in statements:
            bind.execute(sa.text(statement))
    bind.execute(sa.text("SELECT set_config('app.workspace_id', '', true)"))


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table('document_parser'):
        op.create_table('document_parser',
        sa.Column('workspace_id', sa.String(length=64), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('base_url', sa.String(length=500), nullable=False),
        sa.Column('server_url', sa.String(length=500), nullable=True),
        sa.Column('backend', sa.String(length=32), nullable=False),
        sa.Column('api_key_ciphertext', sa.Text(), nullable=True),
        sa.Column('api_key_last4', sa.String(length=4), nullable=True),
        sa.Column('lang', sa.String(length=16), nullable=False),
        sa.Column('max_concurrency', sa.Integer(), nullable=False),
        sa.Column('updated_by', sa.String(length=64), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('workspace_id')
        )
        for statement in _PARSER_RLS:
            op.execute(statement)
    if 'thinking' not in {column['name'] for column in inspector.get_columns('model')}:
        op.add_column('model', sa.Column('thinking', sa.String(length=8), nullable=True))
    op.execute('DROP TABLE IF EXISTS model_group_item')
    op.execute('DROP TABLE IF EXISTS model_group')
    _each_workspace(_DROP_GROUP_GRANTS, _DROP_SNAPSHOT_MODELS)


def downgrade() -> None:
    pass
