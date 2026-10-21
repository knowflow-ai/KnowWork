import shutil
import tempfile
import uuid
from dataclasses import dataclass

import pgserver
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from knowwork_server.conversations.models import Conversation
from knowwork_server.db.base import Base, make_engine, make_sessionmaker


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="session")
def database_url():
    data_dir = tempfile.mkdtemp(prefix="kw-pg-")
    server = pgserver.get_server(data_dir)
    socket_dir = server.get_uri().split("host=")[1]
    yield f"postgresql+asyncpg://postgres@/postgres?host={socket_dir}"
    server.cleanup()
    # pgserver 的 cleanup 只停库不删数据目录，不删会在临时目录里每次留下约 40MB。
    shutil.rmtree(data_dir, ignore_errors=True)


@pytest.fixture
async def sessionmaker(database_url):
    """每个测试一个引擎（asyncpg 连接不能跨事件循环），表结构幂等创建。"""
    engine = make_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield make_sessionmaker(engine)
    await engine.dispose()


@pytest.fixture
async def conversation_id(sessionmaker):
    cid = f"c_{uuid.uuid4().hex}"
    async with sessionmaker() as session:
        session.add(Conversation(id=cid, workspace_id="default", user_id="dev-user", title=""))
        await session.commit()
    return cid


@pytest.fixture
async def fresh_url(database_url):
    """每个测试一个全新的空库（不建表），用于验证迁移与真实启动路径。"""
    name = f"mig_{uuid.uuid4().hex[:12]}"
    admin = create_async_engine(database_url, isolation_level="AUTOCOMMIT")
    async with admin.connect() as connection:
        await connection.execute(text(f'CREATE DATABASE "{name}"'))
    url = database_url.replace("/postgres?", f"/{name}?")
    yield url
    async with admin.connect() as connection:
        await connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    await admin.dispose()


@dataclass(frozen=True)
class AppDb:
    """同一个库的两个账号：owner_url 用于迁移（表的 owner），app_url 是业务运行用的普通账号。"""

    owner_url: str
    app_url: str
    role: str


@pytest.fixture
async def app_db(fresh_url):
    """全新空库加一个普通应用账号：非超级用户、不带 BYPASSRLS、不是任何表的 owner，与生产一致。

    只建角色、不迁移；测试自己用 owner_url 跑 upgrade_to_head(app_role=role) 授权。
    """
    role = f"app_{uuid.uuid4().hex[:8]}"
    owner = create_async_engine(fresh_url, isolation_level="AUTOCOMMIT")
    async with owner.connect() as connection:
        await connection.execute(text(f"CREATE ROLE {role} LOGIN NOSUPERUSER NOBYPASSRLS"))
    yield AppDb(owner_url=fresh_url, app_url=fresh_url.replace("postgres@", f"{role}@", 1), role=role)
    async with owner.connect() as connection:
        # 角色是集群级的：先清掉它在本库的授权与默认权限，再删角色
        await connection.execute(text(f"DROP OWNED BY {role}"))
        await connection.execute(text(f"DROP ROLE {role}"))
    await owner.dispose()
