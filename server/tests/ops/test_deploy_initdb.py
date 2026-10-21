"""部署的数据库初始化脚本：两个角色都不是超级用户、不带 BYPASSRLS。
knowwork_owner 是库与 public 的所有者，只用于迁移；knowwork_app 只有迁移授予的增删改查，
不是任何表的所有者，关不掉行级安全。开了 FORCE ROW LEVEL SECURITY 的表对所有者同样生效。

脚本用 pgserver 自带的 psql 真实执行（与 postgres 官方镜像执行 initdb.d 的方式一致）。
"""

import asyncio
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pgserver
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from knowwork_server.db.migrate import current_revision, upgrade_to_head
from knowwork_server.ops.bootstrap import Layout, run

pytestmark = pytest.mark.anyio

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "deploy" / "postgres" / "initdb" / "10-knowwork-roles.sh"
PG_BIN = Path(pgserver.__file__).parent / "pginstall" / "bin"
ROLES = ("knowwork_app", "knowwork_owner")


def _run_script(env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(["sh", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60, check=False)


def _socket_dir(database_url: str) -> str:
    return database_url.split("host=")[1]


def _url(database_url: str, user: str, database: str) -> str:
    return database_url.replace("postgres@", f"{user}@", 1).replace("/postgres?", f"/{database}?", 1)


async def _drop(server, name: str) -> None:
    async with server.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        for role in ROLES:
            await conn.execute(text(f"DROP ROLE IF EXISTS {role}"))


@pytest.fixture
async def initialized(database_url, tmp_path):
    name = "kw_init_" + uuid.uuid4().hex[:10]
    server = create_async_engine(database_url, isolation_level="AUTOCOMMIT")
    await _drop(server, name)
    async with server.connect() as conn:
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    layout = Layout(secrets_dir=tmp_path / "s", host_dir=tmp_path / "h", db_dir=tmp_path / "d")
    run(layout)
    env = {**os.environ, "PATH": f"{PG_BIN}{os.pathsep}{os.environ['PATH']}", "PGHOST": _socket_dir(database_url),
           "POSTGRES_USER": "postgres", "POSTGRES_DB": name, "KNOWWORK_DB_SECRETS_DIR": str(layout.db_dir)}
    result = await asyncio.to_thread(_run_script, env)
    assert result.returncode == 0, result.stderr
    yield name, result
    await _drop(server, name)
    await server.dispose()


async def test_roles_are_plain_and_owner_owns_database(initialized, database_url):
    name, result = initialized
    assert "password" not in result.stdout.lower()
    engine = create_async_engine(_url(database_url, "postgres", name))
    async with engine.connect() as conn:
        rows = (await conn.execute(text(
            "SELECT rolname, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolreplication FROM pg_roles "
            "WHERE rolname IN ('knowwork_owner', 'knowwork_app')"))).all()
        owner = await conn.scalar(text("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = :n"),
                                  {"n": name})
        schema_owner = await conn.scalar(text("SELECT pg_get_userbyid(nspowner) FROM pg_namespace "
                                              "WHERE nspname = 'public'"))
    await engine.dispose()
    assert sorted(row.rolname for row in rows) == ["knowwork_app", "knowwork_owner"]
    assert not any(any(row[1:]) for row in rows), "两个角色都不是超级用户、不带 BYPASSRLS、不能建库建角色"
    assert owner == schema_owner == "knowwork_owner"


@pytest.fixture
async def migrated(initialized, database_url):
    """按部署路径：owner 迁移并给 app 授权。"""
    name, _ = initialized
    owner_url = _url(database_url, "knowwork_owner", name)
    await upgrade_to_head(owner_url, app_role="knowwork_app")
    return owner_url, _url(database_url, "knowwork_app", name)


async def test_real_migrations_run_as_owner_and_grant_app(migrated):
    owner_url, app_url = migrated
    assert await current_revision(app_url) == await current_revision(owner_url) is not None
    app = create_async_engine(app_url)
    try:
        async with app.begin() as conn:
            await conn.execute(text("INSERT INTO workspace (id, name) VALUES ('w1', 'W')"))
        async with app.begin() as conn:
            await conn.execute(text("SELECT set_config('app.workspace_id', 'w1', true)"))
            await conn.execute(text("INSERT INTO casbin_rule (workspace_id, ptype, v0, v1, v2, v3) "
                                    "VALUES ('w1', 'g', 'a', 'b', 'w1', '')"))
        async with app.begin() as conn:
            assert await conn.scalar(text("SELECT count(*) FROM casbin_rule")) == 0, "不声明空间什么都读不到"
    finally:
        await app.dispose()


async def test_app_role_cannot_escape_row_level_security(migrated):
    _, app_url = migrated
    app = create_async_engine(app_url)
    try:
        for statement in ("ALTER TABLE conversation DISABLE ROW LEVEL SECURITY",
                          "ALTER TABLE conversation NO FORCE ROW LEVEL SECURITY",
                          "DROP POLICY workspace_isolation ON conversation",
                          "CREATE TABLE evil (id int)",
                          "CREATE DATABASE evil", "CREATE ROLE evil"):
            with pytest.raises(DBAPIError):
                async with app.connect() as conn:
                    await conn.execution_options(isolation_level="AUTOCOMMIT")
                    await conn.execute(text(statement))
        with pytest.raises(DBAPIError):  # 不带 BYPASSRLS 时关不掉行级安全
            async with app.begin() as conn:
                await conn.execute(text("SET LOCAL row_security = off"))
                await conn.execute(text("SELECT count(*) FROM conversation"))
    finally:
        await app.dispose()


async def test_owner_is_still_bound_by_forced_rls(migrated):
    owner_url, _ = migrated
    owner = create_async_engine(owner_url)
    try:
        async with owner.begin() as conn:
            await conn.execute(text("SELECT set_config('app.workspace_id', 'a', true)"))
            await conn.execute(text("INSERT INTO conversation (id, workspace_id, user_id, title) "
                                    "VALUES ('c1', 'a', 'u', '')"))
        async with owner.begin() as conn:
            await conn.execute(text("SELECT set_config('app.workspace_id', 'b', true)"))
            assert await conn.scalar(text("SELECT count(*) FROM conversation")) == 0
    finally:
        await owner.dispose()


def test_script_fails_without_secrets(tmp_path):
    env = {"PATH": os.environ["PATH"], "KNOWWORK_DB_SECRETS_DIR": str(tmp_path / "none")}
    result = _run_script(env)
    assert result.returncode != 0
    assert "bootstrap" in result.stderr


@pytest.mark.skipif(shutil.which("sh") is None, reason="需要 POSIX shell")
def test_script_is_posix_sh():
    assert SCRIPT.read_text(encoding="utf-8").startswith("#!/bin/sh\n")
