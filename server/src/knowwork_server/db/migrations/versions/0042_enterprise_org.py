"""企业组织：目录同步来的部门与成员（全系统一份），空间成员规则，空间成员记下怎么进来的

Revision ID: 0042
Revises: 0041
Create Date: 2026-10-31 21:00:00

- org_department、org_member：各目录来源（AD、企业微信……）同步来的部门树与成员，不属于某个空间、不开行级安全；
  权限上物化进各空间的规则（org:<部门> 的上下级、本空间成员属于哪些企业部门）；
- workspace_member_rule：某企业部门（可含下级）的人自动是本空间成员，按空间开行级安全；
- workspace_member.origin：manual 手工加（原有成员都是），rule 按成员规则加（人离开部门时随之移出）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0042'
down_revision: str | None = '0041'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONDITION = "workspace_id = current_setting('app.workspace_id', true)"


def upgrade() -> None:
    op.create_table('org_department',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('source_id', sa.String(length=64), nullable=False),
    sa.Column('external_id', sa.String(length=255), nullable=False),
    sa.Column('parent_id', sa.String(length=64), nullable=True),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('sort', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['parent_id'], ['org_department.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('source_id', 'external_id', name='uq_org_department_external')
    )
    op.create_index(op.f('ix_org_department_parent_id'), 'org_department', ['parent_id'], unique=False)
    op.create_index(op.f('ix_org_department_source_id'), 'org_department', ['source_id'], unique=False)
    op.create_table('org_member',
    sa.Column('department_id', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.String(length=64), nullable=False),
    sa.Column('is_primary', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['department_id'], ['org_department.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('department_id', 'user_id')
    )
    op.create_index(op.f('ix_org_member_user_id'), 'org_member', ['user_id'], unique=False)
    op.create_table('workspace_member_rule',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('workspace_id', sa.String(length=64), nullable=False),
    sa.Column('department_id', sa.String(length=64), nullable=False),
    sa.Column('include_children', sa.Boolean(), nullable=False),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['department_id'], ['org_department.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspace.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('workspace_id', 'department_id', name='uq_workspace_member_rule')
    )
    op.create_index(op.f('ix_workspace_member_rule_workspace_id'), 'workspace_member_rule', ['workspace_id'],
                    unique=False)
    policy = f'CREATE POLICY workspace_isolation ON "workspace_member_rule" USING ({_CONDITION}) WITH CHECK ({_CONDITION})'
    for statement in ('ALTER TABLE "workspace_member_rule" ENABLE ROW LEVEL SECURITY',
                      'ALTER TABLE "workspace_member_rule" FORCE ROW LEVEL SECURITY', policy):
        op.execute(statement)
    op.add_column('workspace_member', sa.Column('origin', sa.String(length=16), server_default='manual',
                                                nullable=False))


def downgrade() -> None:
    op.drop_column('workspace_member', 'origin')
    op.drop_index(op.f('ix_workspace_member_rule_workspace_id'), table_name='workspace_member_rule')
    op.drop_table('workspace_member_rule')
    op.drop_index(op.f('ix_org_member_user_id'), table_name='org_member')
    op.drop_table('org_member')
    op.drop_index(op.f('ix_org_department_source_id'), table_name='org_department')
    op.drop_index(op.f('ix_org_department_parent_id'), table_name='org_department')
    op.drop_table('org_department')
