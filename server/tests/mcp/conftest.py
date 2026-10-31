"""mcp 测试夹具：独立数据库（access 的表加 mcp_server、mcp_connection、mcp_oauth_pending、mcp_workspace_setting，开启行级安全，业务用普通角色读写），
加一个真实的 MCP 服务（tests/mcp_stub.py）。

空间 ws1：owner alice、admin bob、editor carol、editor dave。
"""

import uuid
from dataclasses import dataclass

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from knowwork_server.access.models import ACCESS_RLS_TABLES, ACCESS_TABLES
from knowwork_server.access.rls import enable_row_level_security
from knowwork_server.access.service import AccessService
from knowwork_server.db.base import Base, make_engine, make_sessionmaker
from knowwork_server.tools.mcp import McpModule, build_mcp
from knowwork_server.tools.mcp.models import (
    McpConnectionRow,
    McpOAuthPendingRow,
    McpServerRow,
    McpWorkspaceSettingRow,
)

from ..mcp_oauth_stub import McpOAuthStub
from ..mcp_stub import TOKEN, McpStub
from ..support import free_port

APP_ROLE = "kw_mcp_app"
MASTER = "mcp-test-master-key-".ljust(40, "k")
PUBLIC = "http://kw.test"  # 浏览器访问的外部地址：OAuth 授权的回调地址由它拼出
AUTH = {"Authorization": TOKEN}


@dataclass(frozen=True)
class Env:
    access: AccessService
    mcp: McpModule
    sessionmaker: object
    admin_url: str


def _swap(url: str, *, user: str, database: str) -> str:
    return url.replace("postgres@", f"{user}@", 1).replace("/postgres?", f"/{database}?", 1)


@pytest.fixture
async def mcp_db(database_url):
    name = "kw_mcp_" + uuid.uuid4().hex[:12]
    server = create_async_engine(database_url, isolation_level="AUTOCOMMIT")
    async with server.connect() as conn:
        if not await conn.scalar(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": APP_ROLE}):
            await conn.execute(text(f"CREATE ROLE {APP_ROLE} LOGIN NOSUPERUSER NOBYPASSRLS"))
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    admin_url = _swap(database_url, user="postgres", database=name)
    admin = create_async_engine(admin_url)
    async with admin.begin() as conn:
        tables = [*ACCESS_TABLES, McpServerRow.__table__, McpConnectionRow.__table__, McpOAuthPendingRow.__table__,
                  McpWorkspaceSettingRow.__table__]
        await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
        await enable_row_level_security(conn, (*ACCESS_RLS_TABLES, "mcp_server", "mcp_connection", "mcp_oauth_pending",
                                                 "mcp_workspace_setting"))
        await conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}"))
        await conn.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}"))
        await conn.execute(text(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}"))
    await admin.dispose()
    yield admin_url, _swap(database_url, user=APP_ROLE, database=name)
    async with server.connect() as conn:
        await conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    await server.dispose()


@pytest.fixture
async def env(mcp_db):
    admin_url, app_url = mcp_db
    engine = make_engine(app_url)
    sessionmaker = make_sessionmaker(engine)
    access = AccessService(sessionmaker)
    assert await access.reload()
    await access.create_workspace(workspace_id="ws1", name="空间一", owner_user_id="alice")
    for user, role in (("bob", "admin"), ("carol", "editor"), ("dave", "editor")):
        await access.add_member("alice", "ws1", user, role)
    yield Env(access=access, mcp=build_mcp(MASTER, sessionmaker, access, public_base_url=PUBLIC),
              sessionmaker=sessionmaker, admin_url=admin_url)
    await engine.dispose()


@pytest.fixture
async def stub():
    async with McpStub(free_port()) as server:
        yield server


@pytest.fixture
async def oauth_stub():
    async with McpOAuthStub(free_port()) as server:
        yield server
