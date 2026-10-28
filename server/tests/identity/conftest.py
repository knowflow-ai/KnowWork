"""身份模块测试夹具：每个测试一套干净的身份表、可拨动的时钟、挂好路由的应用。"""

from datetime import UTC, datetime, timedelta
from typing import Annotated

import httpx
import pytest
from fastapi import Depends, FastAPI
from sqlalchemy import text

from knowwork_server.auth.principal import Principal
from knowwork_server.db.base import Base, make_engine, make_sessionmaker
from knowwork_server.identity import IdentityConfig, build_identity
from knowwork_server.identity.api import build_router

IDENTITY_TABLES = '"user", auth_session, auth_invitation, api_key, oidc_login_state, oidc_identity'
BASE_URL = "https://kw.test"
PASSWORD = "correct horse battery"


class FakeClock:
    def __init__(self) -> None:
        # 从现在起算：会话 Cookie 的过期时间按这个钟算，测试客户端却按真实时间丢弃过期 Cookie；
        # 原来写死 2026-10-18，一周后所有要登录的测试都失败（Cookie 被当成过期丢掉）
        self.now = datetime.now(UTC).replace(second=0, microsecond=0)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta) -> None:
        self.now = self.now + timedelta(**delta)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def sessionmaker(database_url):
    engine = make_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.execute(text(f"TRUNCATE {IDENTITY_TABLES} CASCADE"))
    yield make_sessionmaker(engine)
    await engine.dispose()


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def config():
    return IdentityConfig(public_base_url=BASE_URL)


@pytest.fixture
def admins():
    return set()


@pytest.fixture
def identity(sessionmaker, config, clock):
    return build_identity(sessionmaker, config, clock=clock)


def make_app(identity, admins) -> FastAPI:
    app = FastAPI()
    app.include_router(build_router(identity, is_admin=lambda who, workspace_id: who.user_id in admins and workspace_id == "ws1",
                                    can_bind_workspace=lambda who, workspace_id: True))

    Writer = Annotated[Principal, Depends(identity.require_scopes("conversations:write"))]

    @app.post("/api/probe/write")
    async def probe(who: Writer):
        return {"userId": who.user_id, "method": who.auth_method}

    return app


@pytest.fixture
async def client(identity, admins):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=make_app(identity, admins)),
                                 base_url=BASE_URL) as http:
        yield http


@pytest.fixture
async def alice(identity):
    return await identity.users.create_user(email="Alice@Example.com", name="Alice", password=PASSWORD)


async def login(client, email="alice@example.com", password=PASSWORD) -> httpx.Response:
    return await client.post("/api/auth/login", json={"email": email, "password": password})
