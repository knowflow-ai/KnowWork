"""去掉知识源的个人 Key 方式

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-20 19:50:00

2026-10-20 起知识源只有统一账号与签名断言两种认证方式（技术方案 8.4 节），谁能查哪个知识库统一在 KnowWork 里授权：
- 还在用个人 Key 方式的知识源改成统一账号，连接密钥留空，管理员补填 KnowFlow API Key 之前检索会明确报「还没有配置」；
- 删掉存个人 KnowFlow Key 的表。
knowledge_source 开了 FORCE ROW LEVEL SECURITY，改配置时逐个空间声明 app.workspace_id（同 0014）。
回退只重建空表，删掉的个人 Key 找不回来，改过方式的知识源也不改回去。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0015'
down_revision: str | None = '0014'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TO_SHARED = """UPDATE knowledge_source
    SET config = jsonb_set(config, '{auth_mode}', '"shared"'), secret_ciphertext = NULL, secret_last4 = ''
    WHERE config->>'auth_mode' = 'api_key'"""

_RLS = (
    'ALTER TABLE "knowledge_user_credential" ENABLE ROW LEVEL SECURITY',
    'ALTER TABLE "knowledge_user_credential" FORCE ROW LEVEL SECURITY',
    'CREATE POLICY workspace_isolation ON "knowledge_user_credential" USING (workspace_id = current_setting(\'app.workspace_id\', true)) WITH CHECK (workspace_id = current_setting(\'app.workspace_id\', true))',
)


def upgrade() -> None:
    bind = op.get_bind()
    for workspace_id in bind.execute(sa.text("SELECT id FROM workspace")).scalars().all():
        bind.execute(sa.text("SELECT set_config('app.workspace_id', :w, true)"), {"w": workspace_id})
        bind.execute(sa.text(_TO_SHARED))
    bind.execute(sa.text("SELECT set_config('app.workspace_id', '', true)"))
    op.drop_index(op.f('ix_knowledge_user_credential_workspace_id'), table_name='knowledge_user_credential')
    op.drop_table('knowledge_user_credential')


def downgrade() -> None:
    op.create_table('knowledge_user_credential',
    sa.Column('source_id', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.String(length=64), nullable=False),
    sa.Column('workspace_id', sa.String(length=64), nullable=False),
    sa.Column('ciphertext', sa.Text(), nullable=False),
    sa.Column('last4', sa.String(length=4), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['source_id'], ['knowledge_source.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('source_id', 'user_id')
    )
    op.create_index(op.f('ix_knowledge_user_credential_workspace_id'), 'knowledge_user_credential', ['workspace_id'], unique=False)
    for statement in _RLS:
        op.execute(statement)
