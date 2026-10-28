"""skills 测试夹具：每个测试一个独立数据库，建 access 与 skills 的表并开启行级安全，
业务读写用普通角色（非超级用户、无 BYPASSRLS），与 tests/access 的做法相同。"""

import io
import uuid
import zipfile
from dataclasses import dataclass

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from knowwork_server.access.models import ACCESS_RLS_TABLES, ACCESS_TABLES
from knowwork_server.access.rls import enable_row_level_security
from knowwork_server.access.service import AccessService
from knowwork_server.db.base import Base, make_engine, make_sessionmaker
from knowwork_server.skills import SKILL_RLS_TABLES, SKILL_TABLES, LocalBlobStore, build_skills
from knowwork_server.skills.views import Actor

APP_ROLE = "kw_skills_app"
MASTER = "skills-test-master-key-".ljust(40, "k")


@dataclass(frozen=True)
class Env:
    access: AccessService
    skills: object
    blobs: LocalBlobStore
    sessionmaker: object
    admin_url: str


def _swap(url: str, *, user: str, database: str) -> str:
    return url.replace("postgres@", f"{user}@", 1).replace("/postgres?", f"/{database}?", 1)


@pytest.fixture
async def skills_db(database_url):
    name = "kw_skills_" + uuid.uuid4().hex[:12]
    server = create_async_engine(database_url, isolation_level="AUTOCOMMIT")
    async with server.connect() as conn:
        if not await conn.scalar(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": APP_ROLE}):
            await conn.execute(text(f"CREATE ROLE {APP_ROLE} LOGIN NOSUPERUSER NOBYPASSRLS"))
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    admin_url = _swap(database_url, user="postgres", database=name)
    admin = create_async_engine(admin_url)
    async with admin.begin() as conn:
        await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[*ACCESS_TABLES, *SKILL_TABLES]))
        await enable_row_level_security(conn, (*ACCESS_RLS_TABLES, *SKILL_RLS_TABLES))
        await conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}"))
        await conn.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}"))
        await conn.execute(text(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}"))
    await admin.dispose()
    yield admin_url, _swap(database_url, user=APP_ROLE, database=name)
    async with server.connect() as conn:
        await conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    await server.dispose()


@pytest.fixture
async def env(skills_db, tmp_path):
    admin_url, app_url = skills_db
    engine = make_engine(app_url)
    sessionmaker = make_sessionmaker(engine)
    access = AccessService(sessionmaker)
    assert await access.reload()
    await access.create_workspace(workspace_id="ws1", name="空间一", owner_user_id="alice")
    for user, role in (("bob", "admin"), ("carol", "editor"), ("dave", "editor")):
        await access.add_member("alice", "ws1", user, role)
    blobs = LocalBlobStore(tmp_path / "blobs")
    yield Env(access=access, skills=build_skills(MASTER, sessionmaker, access, blobs), blobs=blobs,
              sessionmaker=sessionmaker, admin_url=admin_url)
    await engine.dispose()


def actor(user: str, workspace: str = "ws1") -> Actor:
    return Actor(user_id=user, workspace_id=workspace)


def skill_zip(name: str = "contract-extract", *, version: str = "", allowed: str = "", completion: str = "",
              body: str = "按步骤抽取合同字段。", description: str = "把合同按字段抽成表", folder: str = "") -> bytes:
    lines = [f"name: {name}", f"description: {description}"]
    lines += [f"version: {version}"] if version else []
    lines += [f"allowed-tools: {allowed}"] if allowed else []
    lines += [f"completion-tools: {completion}"] if completion else []
    text_md = "---\n" + "\n".join(lines) + f"\n---\n\n{body}\n"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{folder}SKILL.md", text_md)
        archive.writestr(f"{folder}references/guide.md", "参考资料")
    return buffer.getvalue()
