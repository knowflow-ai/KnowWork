"""models 测试夹具：每个测试一个独立数据库，应用以普通角色（非超级用户、无 BYPASSRLS）连接，
行级安全真实生效。空间 ws1：owner alice，admin bob，editor carol，viewer dave。
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
from knowwork_server.models import MODELS_RLS_TABLES, MODELS_TABLES, build_models_service

APP_ROLE = "kw_models_app"
MASTER_KEY = "m" * 40


@dataclass(frozen=True)
class Db:
    admin_url: str
    app_url: str


def _swap(url: str, *, user: str, database: str) -> str:
    return url.replace("postgres@", f"{user}@", 1).replace("/postgres?", f"/{database}?", 1)


@pytest.fixture
async def db(database_url):
    name = "kw_models_" + uuid.uuid4().hex[:12]
    server = create_async_engine(database_url, isolation_level="AUTOCOMMIT")
    async with server.connect() as conn:
        exists = await conn.scalar(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": APP_ROLE})
        if not exists:
            await conn.execute(text(f"CREATE ROLE {APP_ROLE} LOGIN NOSUPERUSER NOBYPASSRLS"))
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    admin_url = _swap(database_url, user="postgres", database=name)
    admin = create_async_engine(admin_url)
    async with admin.begin() as conn:
        tables = list(ACCESS_TABLES) + list(MODELS_TABLES)
        await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
        await enable_row_level_security(conn, ACCESS_RLS_TABLES + MODELS_RLS_TABLES)
        await conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}"))
        await conn.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}"))
        await conn.execute(text(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}"))
    await admin.dispose()
    yield Db(admin_url=admin_url, app_url=_swap(database_url, user=APP_ROLE, database=name))
    async with server.connect() as conn:
        await conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    await server.dispose()


@pytest.fixture
async def sessionmaker(db):
    engine = make_engine(db.app_url)
    yield make_sessionmaker(engine)
    await engine.dispose()


@pytest.fixture
async def access(sessionmaker):
    svc = AccessService(sessionmaker)
    assert await svc.reload()
    await svc.create_workspace(workspace_id="ws1", name="空间一", owner_user_id="alice")
    for user, role in (("bob", "admin"), ("carol", "editor"), ("dave", "viewer")):
        await svc.add_member("alice", "ws1", user, role)
    return svc


@pytest.fixture
def models(sessionmaker, access):
    return build_models_service(sessionmaker, access, MASTER_KEY)


@pytest.fixture
def catalog(models):
    return models.catalog
