"""通用后台任务表，附件解析任务搬进来

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-22 16:00:00

附件解析原来有自己的任务表 attachment_parse_job，但 worker 从没启动过它的消费者，
需要解析的附件一直排着。这里换成通用任务表 job / job_stage（以后别的后台任务也用它），
把还在排队或运行中的旧任务搬成 attachment.parse 任务重新排队，然后删掉旧表。
旧表开了 FORCE ROW LEVEL SECURITY，迁移账号不是超级用户，搬数据时逐个空间声明 app.workspace_id（同 0015）。
回退时只把还在排队的附件解析任务搬回旧表，其余类型的任务随表删掉。
job_worker 是 worker 心跳（运行面板读它），不分工作空间。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0017'
down_revision: str | None = '0016'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _rls(table: str) -> tuple[str, ...]:
    condition = "workspace_id = current_setting('app.workspace_id', true)"
    return (f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY',
            f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY',
            f'DROP POLICY IF EXISTS workspace_isolation ON "{table}"',
            f'CREATE POLICY workspace_isolation ON "{table}" USING ({condition}) WITH CHECK ({condition})')


# 附件解析的优先级与 attachments/parse_job.py 的 PARSE_PRIORITY 一致：用户在对话里等着，排在批量任务前面
_TO_JOBS = """INSERT INTO job (id, workspace_id, kind, subject_id, pool, priority, payload, version, status, tries,
                     max_tries, next_attempt_at, cancel_requested, created_at, updated_at)
    SELECT id, workspace_id, 'attachment.parse', attachment_id, 'core', 10, '{}'::jsonb, 1, 'queued', 0,
           max_attempts, now(), false, created_at, now()
    FROM attachment_parse_job WHERE status IN ('queued', 'running')"""

_TO_OLD = """INSERT INTO attachment_parse_job (id, workspace_id, attachment_id, status, attempts, max_attempts,
                                      next_attempt_at, created_at, updated_at)
    SELECT DISTINCT ON (j.subject_id) j.id, j.workspace_id, j.subject_id, 'queued', 0, j.max_tries, now(),
           j.created_at, now()
    FROM job j JOIN attachment a ON a.id = j.subject_id
    WHERE j.kind = 'attachment.parse' AND j.status IN ('queued', 'running')
    ORDER BY j.subject_id, j.created_at DESC"""


def _each_workspace(statement: str) -> None:
    bind = op.get_bind()
    for workspace_id in bind.execute(sa.text("SELECT id FROM workspace")).scalars().all():
        bind.execute(sa.text("SELECT set_config('app.workspace_id', :w, true)"), {"w": workspace_id})
        bind.execute(sa.text(statement))
    bind.execute(sa.text("SELECT set_config('app.workspace_id', '', true)"))


def upgrade() -> None:
    op.create_table('job',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('workspace_id', sa.String(length=64), nullable=False),
    sa.Column('kind', sa.String(length=64), nullable=False),
    sa.Column('subject_id', sa.String(length=64), nullable=False),
    sa.Column('pool', sa.String(length=32), nullable=False),
    sa.Column('priority', sa.Integer(), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('tries', sa.Integer(), nullable=False),
    sa.Column('max_tries', sa.Integer(), nullable=False),
    sa.Column('next_attempt_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('lease_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('claim_token', sa.String(length=64), nullable=True),
    sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('stage', sa.String(length=64), nullable=True),
    sa.Column('cancel_requested', sa.Boolean(), nullable=False),
    sa.Column('canceled_by', sa.String(length=64), nullable=True),
    sa.Column('error_code', sa.String(length=64), nullable=True),
    sa.Column('error_class', sa.String(length=16), nullable=True),
    sa.Column('error_detail', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_job_claim', 'job', ['pool', 'status', 'next_attempt_at'], unique=False)
    op.create_index('ix_job_subject', 'job', ['kind', 'subject_id'], unique=False)
    op.create_index(op.f('ix_job_workspace_id'), 'job', ['workspace_id'], unique=False)
    op.create_index('uq_job_queued_subject', 'job', ['workspace_id', 'kind', 'subject_id'], unique=True,
                    postgresql_where=sa.text("status = 'queued'"))
    op.create_table('job_stage',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('workspace_id', sa.String(length=64), nullable=False),
    sa.Column('job_id', sa.String(length=64), nullable=False),
    sa.Column('try_no', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('error_code', sa.String(length=64), nullable=True),
    sa.Column('detail', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['job_id'], ['job.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_job_stage_job_id'), 'job_stage', ['job_id'], unique=False)
    op.create_index(op.f('ix_job_stage_workspace_id'), 'job_stage', ['workspace_id'], unique=False)
    # worker 心跳（运行面板用）：不分工作空间，不开行级安全
    op.create_table('job_worker',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('host', sa.String(length=255), nullable=False),
    sa.Column('pid', sa.Integer(), nullable=False),
    sa.Column('version', sa.String(length=32), nullable=False),
    sa.Column('pools', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('busy', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('kinds', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('gates', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('heartbeat_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    for statement in (*_rls("job"), *_rls("job_stage")):
        op.execute(statement)
    _each_workspace(_TO_JOBS)
    op.drop_index(op.f('ix_attachment_parse_job_workspace_id'), table_name='attachment_parse_job')
    op.drop_index(op.f('ix_attachment_parse_job_status'), table_name='attachment_parse_job')
    op.drop_table('attachment_parse_job')


def downgrade() -> None:
    op.create_table('attachment_parse_job',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('workspace_id', sa.String(length=64), nullable=False),
    sa.Column('attachment_id', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('max_attempts', sa.Integer(), nullable=False),
    sa.Column('next_attempt_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('lease_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('claim_token', sa.String(length=64), nullable=True),
    sa.Column('last_error_code', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['attachment_id'], ['attachment.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('attachment_id')
    )
    op.create_index(op.f('ix_attachment_parse_job_status'), 'attachment_parse_job', ['status'], unique=False)
    op.create_index(op.f('ix_attachment_parse_job_workspace_id'), 'attachment_parse_job', ['workspace_id'], unique=False)
    for statement in _rls("attachment_parse_job"):
        op.execute(statement)
    _each_workspace(_TO_OLD)
    op.drop_table('job_worker')
    op.drop_index(op.f('ix_job_stage_workspace_id'), table_name='job_stage')
    op.drop_index(op.f('ix_job_stage_job_id'), table_name='job_stage')
    op.drop_table('job_stage')
    op.drop_index('uq_job_queued_subject', table_name='job', postgresql_where=sa.text("status = 'queued'"))
    op.drop_index(op.f('ix_job_workspace_id'), table_name='job')
    op.drop_index('ix_job_subject', table_name='job')
    op.drop_index('ix_job_claim', table_name='job')
    op.drop_table('job')
