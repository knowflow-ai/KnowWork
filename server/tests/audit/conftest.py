"""audit 测试夹具：独立数据库，建表与开启行级安全用超级用户，业务读写用普通角色（不绕过 RLS）。

空间 ws1：owner alice、admin bob、editor carol；空间 ws2：owner zed。
"""

import uuid
from dataclasses import dataclass

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from knowwork_server.access.models import ACCESS_RLS_TABLES
from knowwork_server.access.rls import enable_row_level_security
from knowwork_server.access.service import AccessService
from knowwork_server.audit.events import AuditEvent
from knowwork_server.audit.models import AUDIT_RLS_TABLES, AUDIT_TABLES
from knowwork_server.db.base import Base, make_engine, make_sessionmaker
from knowwork_server.db.registry import MODEL_MODULES, load_all_models

APP_ROLE = "kw_audit_app"
TOOL_RLS_TABLES = ("action_receipt", "action_result_blob")


@pytest.fixture(scope="package", autouse=True)
def _detach_until_registered():
    """主线把审计表登记进 db.registry 并写好迁移之前，本包跑完就把 audit_log 从共享元数据上摘下，
    免得 test_migrations 拿元数据和迁移结果比对时多出一张表。登记之后这个夹具自动失效，不必删除。"""
    yield
    if "knowwork_server.audit.models" not in MODEL_MODULES:
        for table in AUDIT_TABLES:
            if table.name in Base.metadata.tables:
                Base.metadata.remove(table)


def _swap(url: str, *, user: str, database: str) -> str:
    return url.replace("postgres@", f"{user}@", 1).replace("/postgres?", f"/{database}?", 1)


async def _prepare(admin_url: str) -> None:
    load_all_models()
    admin = create_async_engine(admin_url)
    async with admin.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await enable_row_level_security(conn, (*ACCESS_RLS_TABLES, *AUDIT_RLS_TABLES, *TOOL_RLS_TABLES))
        for statement in (f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}",
                          f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}",
                          f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}"):
            await conn.execute(text(statement))
    await admin.dispose()


@dataclass(frozen=True)
class Db:
    app_url: str
    admin_url: str


@pytest.fixture
async def db(database_url):
    name = "kw_audit_" + uuid.uuid4().hex[:12]
    server = create_async_engine(database_url, isolation_level="AUTOCOMMIT")
    async with server.connect() as conn:
        if not await conn.scalar(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": APP_ROLE}):
            await conn.execute(text(f"CREATE ROLE {APP_ROLE} LOGIN NOSUPERUSER NOBYPASSRLS"))
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    admin_url = _swap(database_url, user="postgres", database=name)
    await _prepare(admin_url)
    yield Db(app_url=_swap(database_url, user=APP_ROLE, database=name), admin_url=admin_url)
    async with server.connect() as conn:
        await conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    await server.dispose()


@pytest.fixture
async def app_sessionmaker(db):
    engine = make_engine(db.app_url)
    yield make_sessionmaker(engine)
    await engine.dispose()


@pytest.fixture
async def access(app_sessionmaker):
    service = AccessService(app_sessionmaker)
    assert await service.reload()
    await service.create_workspace(workspace_id="ws1", name="空间一", owner_user_id="alice")
    await service.add_member("alice", "ws1", "bob", "admin")
    await service.add_member("alice", "ws1", "carol", "editor")
    await service.create_workspace(workspace_id="ws2", name="空间二", owner_user_id="zed")
    return service


async def count_rows(db: Db, where: str = "TRUE", params: dict | None = None) -> int:
    """超级用户直接数行（绕过行级安全），用来核对写入与清理的真实结果。"""
    engine = create_async_engine(db.admin_url)
    try:
        async with engine.connect() as conn:
            return await conn.scalar(text(f"SELECT count(*) FROM audit_log WHERE {where}"), params or {})
    finally:
        await engine.dispose()


class MemorySink:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def record(self, event: AuditEvent) -> None:
        self.events.append(event)


@pytest.fixture
def memory_sink():
    return MemorySink()
