"""初始化向导：建首个系统管理员与默认空间；并发下只成功一次；完成后永久关闭。

应用以普通角色（非超级用户、无 BYPASSRLS）连接，与部署一致。
"""

import asyncio
import uuid

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from knowwork_server.access.models import ACCESS_RLS_TABLES
from knowwork_server.access.rls import enable_row_level_security
from knowwork_server.access.service import AccessService
from knowwork_server.access.vocab import WORKSPACE_RESOURCE
from knowwork_server.db.base import Base, make_engine, make_sessionmaker
from knowwork_server.identity.models import User
from knowwork_server.identity.users import UserService
from knowwork_server.setup import (
    AlreadyInitialized,
    SetupInconsistent,
    SetupInProgress,
    SystemSetup,
    build_router,
    build_setup_service,
)
from knowwork_server.setup.service import current_system_admin

pytestmark = pytest.mark.anyio

APP_ROLE = "kw_setup_app"
PASSWORD = "correct horse battery"


def _swap(url: str, *, user: str, database: str) -> str:
    return url.replace("postgres@", f"{user}@", 1).replace("/postgres?", f"/{database}?", 1)


@pytest.fixture
async def app_url(database_url):
    name = "kw_setup_" + uuid.uuid4().hex[:12]
    server = create_async_engine(database_url, isolation_level="AUTOCOMMIT")
    async with server.connect() as conn:
        if not await conn.scalar(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": APP_ROLE}):
            await conn.execute(text(f"CREATE ROLE {APP_ROLE} LOGIN NOSUPERUSER NOBYPASSRLS"))
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    admin = create_async_engine(_swap(database_url, user="postgres", database=name))
    async with admin.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await enable_row_level_security(conn, ACCESS_RLS_TABLES)
        await conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}"))
        await conn.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}"))
        await conn.execute(text(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}"))
    await admin.dispose()
    yield _swap(database_url, user=APP_ROLE, database=name)
    async with server.connect() as conn:
        await conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    await server.dispose()


@pytest.fixture
async def ctx(app_url):
    engine = make_engine(app_url)
    sessionmaker = make_sessionmaker(engine)
    access = AccessService(sessionmaker)
    assert await access.reload()
    yield sessionmaker, access, build_setup_service(sessionmaker, access)
    await engine.dispose()


def _client(service) -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(build_router(service))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def test_initialize_creates_admin_and_default_workspace(ctx):
    sessionmaker, access, service = ctx
    assert (await service.status()).initialized is False
    result = await service.initialize(email="Admin@Example.com", name="管理员", password=PASSWORD)
    assert result.workspace_id == "default"
    assert (await service.status()).initialized is True
    assert await service.is_system_admin(result.admin_user_id) is True
    assert await service.is_system_admin("someone-else") is False
    assert access.check(result.admin_user_id, "default", WORKSPACE_RESOURCE, "owner") is True
    assert access.is_system_admin(result.admin_user_id), "初始化完成就告诉权限模块：系统管理员能进任何空间"
    user = await UserService(sessionmaker).authenticate("admin@example.com", PASSWORD)
    assert user is not None and user.id == result.admin_user_id


async def test_closed_after_completion(ctx):
    _, _, service = ctx
    await service.initialize(email="a@example.com", name="甲", password=PASSWORD)
    with pytest.raises(AlreadyInitialized):
        await service.initialize(email="b@example.com", name="乙", password=PASSWORD)
    async with _client(service) as client:
        response = await client.post("/api/setup", json={"email": "c@example.com", "name": "丙", "password": PASSWORD})
        assert response.status_code == 409
        assert (await client.get("/api/setup")).json() == {"initialized": True}


async def test_twenty_concurrent_requests_only_one_succeeds(ctx):
    sessionmaker, _, service = ctx
    async with _client(service) as client:
        responses = await asyncio.gather(*(
            client.post("/api/setup", json={"email": f"u{i}@example.com", "name": f"用户{i}", "password": PASSWORD})
            for i in range(20)
        ))
    codes = sorted(response.status_code for response in responses)
    assert codes == [201] + [409] * 19
    async with sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(User)) == 1
        row = await session.get(SystemSetup, 1)
    winner = next(r for r in responses if r.status_code == 201).json()
    assert row.admin_user_id == winner["admin_user_id"]


async def test_lock_held_elsewhere_is_rejected_immediately(ctx):
    sessionmaker, _, service = ctx
    await service._ensure_row()
    async with sessionmaker() as holder, holder.begin():
        await holder.execute(select(SystemSetup).where(SystemSetup.id == 1).with_for_update())
        with pytest.raises(SetupInProgress):
            await asyncio.wait_for(service.initialize(email="x@example.com", name="x", password=PASSWORD), 5)
    assert (await service.status()).initialized is False


async def test_invalid_input_does_not_consume_setup(ctx):
    _, _, service = ctx
    async with _client(service) as client:
        bad = await client.post("/api/setup", json={"email": "not-an-email", "name": "x", "password": PASSWORD})
        assert bad.status_code == 422
        short = await client.post("/api/setup", json={"email": "a@example.com", "name": "x", "password": "short"})
        assert short.status_code == 422
        assert (await client.get("/api/setup")).json() == {"initialized": False}
        ok = await client.post("/api/setup", json={"email": "a@example.com", "name": "x", "password": PASSWORD})
        assert ok.status_code == 201


async def test_half_initialized_state_fails_closed(ctx):
    _, access, service = ctx
    await access.create_workspace(workspace_id="default", name="遗留", owner_user_id="orphan")
    with pytest.raises(SetupInconsistent):
        await service.initialize(email="a@example.com", name="甲", password=PASSWORD)
    async with _client(service) as client:
        response = await client.post("/api/setup", json={"email": "a@example.com", "name": "甲", "password": PASSWORD})
    assert response.status_code == 503
    assert "orphan" not in response.text
    assert (await service.status()).initialized is False


async def test_system_admin_is_restored_into_access_on_startup(ctx):
    """重启以后权限模块是新的：从初始化记录里读回系统管理员（Runtime.start 调用）。"""
    sessionmaker, _, service = ctx
    result = await service.initialize(email="admin@example.com", name="管理员", password=PASSWORD)
    fresh = AccessService(sessionmaker)
    assert await fresh.reload()
    assert not fresh.is_system_admin(result.admin_user_id)
    fresh.set_system_admin(await current_system_admin(sessionmaker))
    assert fresh.is_system_admin(result.admin_user_id)
