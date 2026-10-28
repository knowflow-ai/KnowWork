"""每轮汇总与知识检索记录

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-26 23:30:00

每轮汇总（run_summary）：一轮一行，回合开始时插、结束时补齐（提问、状态、起止时间、用过的工具、各模块补的事实）。
管理端「运行记录」的列表改读它，不再每次从原始事件帧里现凑；以后的问答大盘、知识库运营也从它算。已有的回合按原始帧
回填（提问取事件投影里用户那条消息，工具取 tool/call 帧）。

知识检索记录：聊天里每次知识检索记一条（查了什么、用了哪些条件、按名次的命中与引用编号），回合结束时补轮次号、
按回答里的 [n] 标出被引用的命中。和引用目录一样不对会话表建外键，删会话时一起删。三张表都按工作空间做行级安全。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0024'
down_revision: str | None = '0023'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ('knowledge_search_log', 'knowledge_search_hit', 'run_summary')
# 行级安全的 DDL 写死在迁移里：以后改应用代码不会改变历史迁移的重放结果。
RLS_STATEMENTS = tuple(statement for table in TABLES for statement in (
    f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY',
    f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY',
    f'DROP POLICY IF EXISTS workspace_isolation ON "{table}"',
    f'CREATE POLICY workspace_isolation ON "{table}" USING (workspace_id = current_setting(\'app.workspace_id\', true)) WITH CHECK (workspace_id = current_setting(\'app.workspace_id\', true))',
))

# 已有的回合：每个 turn/start 帧一行；结束原因取同一轮的 turn/end 帧，状态归成四种（同 conversations/summaries.py）
_BACKFILL = """
INSERT INTO run_summary (conversation_id, turn, workspace_id, user_id, question, status, end_reason, started_at,
                         ended_at, tools, facts)
SELECT s.conversation_id, (s.data->'data'->>'turn')::int, s.workspace_id, c.user_id, COALESCE(LEFT(q.text, 2000), ''),
       CASE WHEN e.at IS NULL THEN 'running'
            WHEN e.reason IN ('completed', 'max-tokens') THEN 'completed'
            WHEN e.reason IN ('aborted', 'cancelled', 'interrupted') THEN 'cancelled'
            ELSE 'failed' END,
       LEFT(e.reason, 32), s.created_at, e.at, COALESCE(t.tools, '[]'::jsonb), '{}'::jsonb
FROM run_event s
JOIN conversation c ON c.id = s.conversation_id
LEFT JOIN LATERAL (
    SELECT x.data->'data'->'reason'->>'kind' AS reason, x.created_at AS at FROM run_event x
    WHERE x.conversation_id = s.conversation_id AND x.kind = 'session/event' AND x.data->>'type' = 'turn/end'
      AND x.data->'data'->>'turn' = s.data->'data'->>'turn'
    ORDER BY x.seq LIMIT 1) e ON true
LEFT JOIN LATERAL (
    SELECT u.ui->0->'data'->>'text' AS text FROM run_event u
    WHERE u.conversation_id = s.conversation_id AND u.kind = 'session/event' AND u.data->>'type' = 'user/message'
      AND u.ui->0->>'run_id' = 'run-' || (s.data->'data'->>'turn') AND u.ui->0->'data'->>'role' = 'user'
    ORDER BY u.seq LIMIT 1) q ON true
LEFT JOIN LATERAL (
    SELECT jsonb_agg(called.name ORDER BY called.first) AS tools FROM (
        SELECT x.data->'data'->>'name' AS name, MIN(x.seq) AS first FROM run_event x
        WHERE x.conversation_id = s.conversation_id AND x.kind = 'session/event' AND x.data->>'type' = 'tool/call'
          AND x.data->'data'->>'turn' = s.data->'data'->>'turn' AND x.data->'data'->>'name' IS NOT NULL
        GROUP BY 1) called) t ON true
WHERE s.kind = 'session/event' AND s.data->>'type' = 'turn/start'
  AND jsonb_typeof(s.data->'data'->'turn') = 'number'
ON CONFLICT DO NOTHING
"""


def _each_workspace(statement: str) -> None:
    """表开了 FORCE 行级安全，迁移账号是 owner 也受它管：逐个空间声明了再回填。"""
    bind = op.get_bind()
    for workspace_id in bind.execute(sa.text("SELECT id FROM workspace")).scalars().all():
        bind.execute(sa.text("SELECT set_config('app.workspace_id', :w, true)"), {"w": workspace_id})
        bind.execute(sa.text(statement))
    bind.execute(sa.text("SELECT set_config('app.workspace_id', '', true)"))


def upgrade() -> None:
    op.create_table(
        'run_summary',
        sa.Column('conversation_id', sa.String(length=64), nullable=False),
        sa.Column('turn', sa.Integer(), nullable=False),
        sa.Column('workspace_id', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.String(length=64), nullable=False),
        sa.Column('question', sa.String(length=2000), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('end_reason', sa.String(length=32), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('tools', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('facts', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversation.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('conversation_id', 'turn'),
    )
    op.create_index('ix_run_summary_workspace_started', 'run_summary', ['workspace_id', 'started_at'])
    op.create_table(
        'knowledge_search_log',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('workspace_id', sa.String(length=64), nullable=False),
        sa.Column('conversation_id', sa.String(length=64), nullable=False),
        sa.Column('turn', sa.Integer(), nullable=True),
        sa.Column('user_id', sa.String(length=64), nullable=False),
        sa.Column('query', sa.String(length=1000), nullable=False),
        sa.Column('options', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_knowledge_search_log_workspace_id'), 'knowledge_search_log', ['workspace_id'])
    op.create_index(op.f('ix_knowledge_search_log_conversation_id'), 'knowledge_search_log', ['conversation_id'])
    op.create_index(op.f('ix_knowledge_search_log_created_at'), 'knowledge_search_log', ['created_at'])
    op.create_table(
        'knowledge_search_hit',
        sa.Column('log_id', sa.String(length=64), nullable=False),
        sa.Column('rank', sa.Integer(), nullable=False),
        sa.Column('workspace_id', sa.String(length=64), nullable=False),
        sa.Column('number', sa.Integer(), nullable=True),
        sa.Column('source_id', sa.String(length=64), nullable=False),
        sa.Column('kb_id', sa.String(length=64), nullable=False),
        sa.Column('doc_ref', sa.String(length=500), nullable=False),
        sa.Column('span_ref', sa.String(length=500), nullable=False),
        sa.Column('title', sa.String(length=500), nullable=False),
        sa.Column('score', sa.Float(), nullable=True),
        sa.Column('cited', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['log_id'], ['knowledge_search_log.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('log_id', 'rank'),
    )
    op.create_index(op.f('ix_knowledge_search_hit_workspace_id'), 'knowledge_search_hit', ['workspace_id'])
    op.create_index(op.f('ix_knowledge_search_hit_kb_id'), 'knowledge_search_hit', ['kb_id'])
    for statement in RLS_STATEMENTS:
        op.execute(statement)
    _each_workspace(_BACKFILL)


def downgrade() -> None:
    op.drop_table('knowledge_search_hit')
    op.drop_table('knowledge_search_log')
    op.drop_table('run_summary')
