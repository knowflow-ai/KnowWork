"""PostgreSQL 行级安全：按 app.workspace_id 隔离各工作空间的数据。

- enable_row_level_security：建表之后调用（主线整合进迁移）；
- set_workspace：每个请求的事务开头调用，等价于 SET LOCAL，事务结束即失效。

不设置变量时 current_setting(..., true) 为 NULL 或空串，策略不成立，查不到任何行。
注意：超级用户与带 BYPASSRLS 的角色不受行级安全约束，应用必须用普通角色连接。
"""

import re
from collections.abc import Iterable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

SETTING = "app.workspace_id"
POLICY_NAME = "workspace_isolation"
_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _quote(name: str) -> str:
    if not _IDENT.fullmatch(name):
        raise ValueError(f"illegal table name: {name!r}")
    return f'"{name}"'


def row_level_security_statements(table_names: Iterable[str]) -> tuple[str, ...]:
    """返回开启行级安全所需的 DDL，供迁移脚本直接执行。幂等：策略先删后建。"""
    statements: list[str] = []
    for name in table_names:
        table = _quote(name)
        condition = f"workspace_id = current_setting('{SETTING}', true)"
        statements += [
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
            f"DROP POLICY IF EXISTS {POLICY_NAME} ON {table}",
            f"CREATE POLICY {POLICY_NAME} ON {table} USING ({condition}) WITH CHECK ({condition})",
        ]
    return tuple(statements)


async def enable_row_level_security(connection: AsyncConnection, table_names: Iterable[str]) -> None:
    for statement in row_level_security_statements(table_names):
        await connection.execute(text(statement))


async def set_workspace(session: AsyncSession | AsyncConnection, workspace_id: str) -> None:
    """在当前事务内设置工作空间。必须已在事务中，否则设置随语句结束失效。"""
    if not workspace_id:
        raise ValueError("workspace_id is required")
    await session.execute(text("SELECT set_config(:name, :value, true)"), {"name": SETTING, "value": workspace_id})
