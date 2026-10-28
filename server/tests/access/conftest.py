"""access 测试夹具：每个测试一个独立数据库，应用以普通角色（非超级用户、无 BYPASSRLS）连接。

pgserver 的默认用户 postgres 是超级用户，会绕过行级安全，所以建表与开启 RLS 用它，
业务读写一律用新建的普通角色。
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

APP_ROLE = "kw_access_app"


@dataclass(frozen=True)
class AccessDb:
    admin_url: str
    app_url: str


def _swap(url: str, *, user: str, database: str) -> str:
    return url.replace("postgres@", f"{user}@", 1).replace("/postgres?", f"/{database}?", 1)


@pytest.fixture
async def access_db(database_url):
    name = "kw_access_" + uuid.uuid4().hex[:12]
    server = create_async_engine(database_url, isolation_level="AUTOCOMMIT")
    async with server.connect() as conn:
        exists = await conn.scalar(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": APP_ROLE})
        if not exists:
            await conn.execute(text(f"CREATE ROLE {APP_ROLE} LOGIN NOSUPERUSER NOBYPASSRLS"))
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    admin_url = _swap(database_url, user="postgres", database=name)
    admin = create_async_engine(admin_url)
    async with admin.begin() as conn:
        await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=list(ACCESS_TABLES)))
        await enable_row_level_security(conn, ACCESS_RLS_TABLES)
        await conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}"))
        await conn.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}"))
        await conn.execute(text(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}"))
    await admin.dispose()
    yield AccessDb(admin_url=admin_url, app_url=_swap(database_url, user=APP_ROLE, database=name))
    async with server.connect() as conn:
        await conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    await server.dispose()


@pytest.fixture
async def service(access_db):
    engine = make_engine(access_db.app_url)
    svc = AccessService(make_sessionmaker(engine))
    assert await svc.reload()
    yield svc
    await engine.dispose()


@pytest.fixture
async def ws(service):
    """一个默认空间：owner 为 alice，另有 admin bob、editor carol、editor dave。"""
    await service.create_workspace(workspace_id="ws1", name="空间一", owner_user_id="alice")
    for user, role in (("bob", "admin"), ("carol", "editor"), ("dave", "editor")):
        await service.add_member("alice", "ws1", user, role)
    return "ws1"
