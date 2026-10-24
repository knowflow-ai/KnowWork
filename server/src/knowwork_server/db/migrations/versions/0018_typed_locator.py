"""引用定位带类型，引用加图片与版本提示

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-22 18:00:00

知识源协议的定位改版：Locator 加 type（pdf / docx / slide / sheet / text），Box 从「知识源的版面坐标」
改成占页面的比例（0–1）。已存的引用都来自 KnowFlow，旧框是没有页面尺寸的版面坐标，换算不成比例，
这里改成只留页码与章节路径的 pdf 定位（之前前端也没用到框）。
citation 表加 images（图片列表）、version_note（版本提示）与 locators（片段里每个版面块的定位）。
citation 开了 FORCE ROW LEVEL SECURITY，迁移账号不是超级用户，逐个空间声明 app.workspace_id（同 0015）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0018'
down_revision: str | None = '0017'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TYPED = """UPDATE citation SET locator = jsonb_strip_nulls(jsonb_build_object(
        'type', 'pdf', 'page', locator->'page', 'section_path', COALESCE(locator->'section_path', '[]'::jsonb)))
    WHERE locator IS NOT NULL AND NOT locator ? 'type'"""

# 回退：旧模型只认 page、boxes、section_path，别的类型的定位旧代码看不懂，只留章节路径
_UNTYPED = """UPDATE citation SET locator = jsonb_strip_nulls(jsonb_build_object(
        'page', CASE WHEN locator->>'type' = 'pdf' THEN locator->'page' END,
        'section_path', COALESCE(locator->'section_path', '[]'::jsonb)))
    WHERE locator IS NOT NULL AND locator ? 'type'"""


def _each_workspace(statement: str) -> None:
    bind = op.get_bind()
    for workspace_id in bind.execute(sa.text("SELECT id FROM workspace")).scalars().all():
        bind.execute(sa.text("SELECT set_config('app.workspace_id', :w, true)"), {"w": workspace_id})
        bind.execute(sa.text(statement))
    bind.execute(sa.text("SELECT set_config('app.workspace_id', '', true)"))


def upgrade() -> None:
    op.add_column('citation', sa.Column('images', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('citation', sa.Column('version_note', sa.String(length=300), nullable=True))
    op.add_column('citation', sa.Column('locators', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    _each_workspace(_TYPED)


def downgrade() -> None:
    _each_workspace(_UNTYPED)
    op.drop_column('citation', 'locators')
    op.drop_column('citation', 'version_note')
    op.drop_column('citation', 'images')
