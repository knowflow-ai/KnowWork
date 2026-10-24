"""扩展的迁移分支和开源主线一起升级到最新。

扩展分支依赖主线的某个版本（depends_on），Alembic 在版本表里只留没被别人依赖的那几条：
扩展依赖的正好是主线最新时只有扩展一行，主线之后又出了新版本就是两行，所以期望值从迁移脚本算。
"""

from pathlib import Path

import pytest
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from knowwork_server.db.migrate import Branches, alembic_config, current_revision, upgrade_to_head

pytestmark = pytest.mark.anyio
DEMO = Branches(locations=(Path(__file__).with_name("demo_migrations"),))


def heads(branches: Branches) -> str:
    """升到最新后版本表里该有的几行（被别的分支依赖着的不单列），和 current_revision 一样排序、逗号连起来。"""
    script = ScriptDirectory.from_config(alembic_config("postgresql://", branches))
    found = set(script.get_heads())
    covered = {rev.revision for head in found for rev in script.revision_map.iterate_revisions(head, "base")
               if rev.revision != head}
    return ", ".join(sorted(found - covered))


async def tables(url):
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            rows = await connection.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))
            return {row[0] for row in rows}
    finally:
        await engine.dispose()


async def test_extension_branch_upgrades_with_the_main_line(fresh_url):
    await upgrade_to_head(fresh_url, branches=DEMO)
    assert {"job", "demo_note"} <= await tables(fresh_url)
    assert "demo_0001" in heads(DEMO)
    assert await current_revision(fresh_url) == heads(DEMO)
    await upgrade_to_head(fresh_url, branches=DEMO)  # 再跑一遍什么都不做
    assert await current_revision(fresh_url) == heads(DEMO)


async def test_installing_an_extension_later_adds_only_its_branch(fresh_url):
    await upgrade_to_head(fresh_url, branches=Branches())
    assert "demo_note" not in await tables(fresh_url)
    await upgrade_to_head(fresh_url, branches=DEMO)
    assert "demo_note" in await tables(fresh_url)
