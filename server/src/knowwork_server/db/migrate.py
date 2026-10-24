"""Alembic 迁移入口。

- upgrade_to_head：在一个事务里把库升级到最新版本，可选给应用账号授权；
- migrate_for_settings：业务服务启动时调用，按配置选迁移账号与应用账号；
- 命令行：`python -m knowwork_server.db.migrate revision "说明"` 在空库上自动生成新迁移。

并发：多个实例同时启动时，先取事务级 advisory 锁再跑 Alembic，后到的排队等前一个提交，
随后看到版本已是最新便什么都不做。取锁之后才设 lock_timeout：等别的实例迁移完是正常的，
但 DDL 被运行中的业务连接挡住时要按时失败，而不是让启动无限期挂住。

账号分离：迁移账号是表的 owner；应用账号只拿增删改查与序列权限，不是 owner，
因而不能 `ALTER TABLE ... DISABLE ROW LEVEL SECURITY`，行级安全对它始终生效。
授权与迁移在同一事务里，授权失败整个迁移回滚。

迁移脚本放在本包的 migrations/ 下，随 Python 包一起发布，不依赖工作目录里的 alembic.ini。
扩展各带一条自己的迁移分支：已安装的扩展的迁移目录追加进 version_locations，升级到全部分支的
最新（heads）。扩展只加自己的表，不改开源表。
"""

import asyncio
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import create_async_engine

MIGRATIONS_DIR = Path(__file__).with_name("migrations")
# 全部 KnowWork 实例共用的迁移锁编号（ASCII "KWMIGRAT"）。不能改，否则新旧版本的实例互不排斥。
MIGRATION_LOCK_KEY = 0x4B574D4947524154
DEFAULT_LOCK_TIMEOUT_SECONDS = 30.0
_ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")

Step = Callable[[Connection], None]


class MigrationSettings(Protocol):
    database_url: str
    migration_database_url: str
    app_db_role: str
    migration_lock_timeout_seconds: float


def validate_role_name(role: str) -> str:
    """角色名只允许小写字母、数字、下划线且不以数字开头（PostgreSQL 未加引号标识符的安全子集）。"""
    if not isinstance(role, str) or not _ROLE_NAME.fullmatch(role):
        raise ValueError("应用数据库角色名不合法：只允许小写字母、数字、下划线，不以数字开头，最长 63 个字符")
    return role


@dataclass(frozen=True)
class Branches:
    """主线之外要一起升级的迁移：扩展的迁移目录与它们的模型模块（env.py 据此加载模型）。"""

    locations: tuple[Path, ...] = ()
    model_modules: tuple[str, ...] = ()


def _installed_branches() -> Branches:
    from ..extensions import installed_extensions

    extensions = installed_extensions()
    return Branches(locations=extensions.migration_dirs, model_modules=extensions.model_modules)


MAIN_LINE = Branches()


def alembic_config(url: str, branches: Branches = MAIN_LINE) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("path_separator", "os")
    if branches.locations:
        # 设了 version_locations 就不再自动包含主线目录，要显式列上
        locations = (MIGRATIONS_DIR / "versions", *branches.locations)
        config.set_main_option("version_locations", os.pathsep.join(str(path) for path in locations))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    config.attributes["model_modules"] = branches.model_modules
    return config


async def _run(url: str, action: Callable[[Config], None], before: Step | None = None,
               after: Step | None = None, branches: Branches | None = None) -> None:
    branches = _installed_branches() if branches is None else branches
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            def _sync(sync_connection: Connection) -> None:
                if before is not None:
                    before(sync_connection)
                config = alembic_config(url, branches)
                config.attributes["connection"] = sync_connection
                action(config)
                if after is not None:
                    after(sync_connection)

            await connection.run_sync(_sync)
    finally:
        await engine.dispose()


def _lock_step(lock_timeout_seconds: float) -> Step:
    if not lock_timeout_seconds > 0:
        raise ValueError("lock_timeout_seconds 必须大于 0")
    value = f"{max(int(lock_timeout_seconds * 1000), 1)}ms"

    def run(connection: Connection) -> None:
        connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": MIGRATION_LOCK_KEY})
        connection.execute(text("SELECT set_config('lock_timeout', :value, true)"), {"value": value})

    return run


def _check_app_role(connection: Connection, role: str) -> None:
    """应用账号必须存在，且不是迁移账号本身、超级用户或带 BYPASSRLS 的角色，否则行级安全形同虚设。"""
    row = connection.execute(
        text("SELECT rolname = current_user, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = :role"),
        {"role": role},
    ).one_or_none()
    if row is None:
        raise RuntimeError("应用数据库角色不存在，请先创建")
    if any(row):
        raise RuntimeError("应用数据库角色不能是迁移账号、超级用户或带 BYPASSRLS 的角色")


def grant_statements(role: str) -> tuple[str, ...]:
    """给应用账号的授权：现有的表与序列，以及迁移账号以后在 public 下新建的表与序列。"""
    quoted = f'"{validate_role_name(role)}"'
    return (
        f"GRANT USAGE ON SCHEMA public TO {quoted}",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {quoted}",
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {quoted}",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {quoted}",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {quoted}",
        # 版本表只读：应用账号改不了迁移记录
        f"REVOKE INSERT, UPDATE, DELETE ON alembic_version FROM {quoted}",
    )


def _grant_step(role: str) -> Step:
    statements = grant_statements(role)

    def run(connection: Connection) -> None:
        _check_app_role(connection, role)
        for statement in statements:
            connection.execute(text(statement))

    return run


async def upgrade_to_head(url: str, *, app_role: str | None = None,
                          lock_timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
                          branches: Branches | None = None) -> None:
    """用 url 的账号（成为表的 owner）升级到最新；给了 app_role 就在同一事务里给它授权。
    branches 不给时取本进程已安装的扩展；升级到全部分支的最新。"""
    before = _lock_step(lock_timeout_seconds)
    after = _grant_step(app_role) if app_role is not None else None
    await _run(url, lambda config: command.upgrade(config, "heads"), before=before, after=after, branches=branches)


async def migrate_for_settings(settings: MigrationSettings) -> None:
    """启动时的迁移：设了 migration_database_url 就用它迁移，否则沿用 database_url。"""
    await upgrade_to_head(settings.migration_database_url or settings.database_url,
                          app_role=settings.app_db_role or None,
                          lock_timeout_seconds=settings.migration_lock_timeout_seconds)


async def current_revision(url: str) -> str | None:
    from alembic.runtime.migration import MigrationContext

    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            # 装了扩展时版本表里每条分支一行，get_current_revision 遇到多行会报错；多行时用逗号连起来
            heads = await connection.run_sync(lambda sync: MigrationContext.configure(sync).get_current_heads())
            return ", ".join(sorted(heads)) or None
    finally:
        await engine.dispose()


def _main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] != "revision":
        print('用法：python -m knowwork_server.db.migrate revision "说明"'
              "（需设置 KNOWWORK_MIGRATION_DATABASE_URL 或 KNOWWORK_DATABASE_URL，指向已升级到最新的库）")
        return 2
    url = os.environ.get("KNOWWORK_MIGRATION_DATABASE_URL") or os.environ.get("KNOWWORK_DATABASE_URL")
    if not url:
        print("缺少 KNOWWORK_DATABASE_URL")
        return 2
    asyncio.run(_run(url, lambda config: command.revision(config, message=argv[1], autogenerate=True)))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
