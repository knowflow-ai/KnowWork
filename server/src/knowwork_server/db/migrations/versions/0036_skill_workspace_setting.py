"""技能：空间里的上层技能除了停用，还能收窄给谁用

Revision ID: 0036
Revises: 0035
Create Date: 2026-10-30 22:00:00

skill_workspace_off 改成 skill_workspace_setting：一行记空间管理员对一个到达本空间的上层技能（企业技能、内置技能）
的设置，off 停用、narrowed 收窄给谁用（选中的对象存成本空间的授权）。原有的行都是停用，迁过来 off 为真。
直接改表名（行级安全与策略跟着表走），约束名一并改掉，和新建出来的表一致。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0036'
down_revision: str | None = '0035'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.rename_table('skill_workspace_off', 'skill_workspace_setting')
    op.execute('ALTER TABLE skill_workspace_setting RENAME CONSTRAINT skill_workspace_off_pkey '
               'TO skill_workspace_setting_pkey')
    op.execute('ALTER TABLE skill_workspace_setting RENAME CONSTRAINT skill_workspace_off_workspace_id_fkey '
               'TO skill_workspace_setting_workspace_id_fkey')
    op.alter_column('skill_workspace_setting', 'disabled_by', new_column_name='updated_by')
    op.alter_column('skill_workspace_setting', 'disabled_at', new_column_name='updated_at')
    # 已有的行都是停用：先按真加列，再把默认值改成假
    op.add_column('skill_workspace_setting', sa.Column('off', sa.Boolean(), server_default=sa.true(), nullable=False))
    op.alter_column('skill_workspace_setting', 'off', server_default=sa.false())
    op.add_column('skill_workspace_setting',
                  sa.Column('narrowed', sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade() -> None:
    op.execute('DELETE FROM skill_workspace_setting WHERE NOT off')
    op.drop_column('skill_workspace_setting', 'narrowed')
    op.drop_column('skill_workspace_setting', 'off')
    op.alter_column('skill_workspace_setting', 'updated_at', new_column_name='disabled_at')
    op.alter_column('skill_workspace_setting', 'updated_by', new_column_name='disabled_by')
    op.execute('ALTER TABLE skill_workspace_setting RENAME CONSTRAINT skill_workspace_setting_workspace_id_fkey '
               'TO skill_workspace_off_workspace_id_fkey')
    op.execute('ALTER TABLE skill_workspace_setting RENAME CONSTRAINT skill_workspace_setting_pkey '
               'TO skill_workspace_off_pkey')
    op.rename_table('skill_workspace_setting', 'skill_workspace_off')
