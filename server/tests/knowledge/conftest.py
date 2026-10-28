"""knowledge 测试夹具：独立数据库，建表与开启行级安全用超级用户，业务读写用普通角色（不绕过 RLS）。

空间 ws1：owner alice、admin bob、editor carol、editor dave；alice、bob、carol 在账号表里有邮箱，dave 已停用。
"""

import uuid
from dataclasses import dataclass

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from knowwork_server.access.models import ACCESS_RLS_TABLES, ACCESS_TABLES
from knowwork_server.access.rls import enable_row_level_security
from knowwork_server.access.service import AccessService
from knowwork_server.db.base import Base, make_engine, make_sessionmaker
from knowwork_server.identity.models import User
from knowwork_server.jobs import JOB_RLS_TABLES, JOB_TABLES
from knowwork_server.knowledge import KNOWLEDGE_RLS_TABLES, KNOWLEDGE_TABLES, build_knowledge
from knowwork_server.tools.models import ActionReceipt, ActionResultBlob

from .knowflow_stub import KnowFlowStub, StubUser, chunk

APP_ROLE = "kw_knowledge_app"
MASTER = "m" * 40
USERS = (("alice", "alice@corp.com", "active"), ("bob", "bob@corp.com", "active"),
         ("carol", "carol@corp.com", "active"), ("dave", "dave@corp.com", "disabled"),
         ("erin", "erin@corp.com", "active"))


@dataclass(frozen=True)
class Db:
    app_url: str


def _swap(url: str, *, user: str, database: str) -> str:
    return url.replace("postgres@", f"{user}@", 1).replace("/postgres?", f"/{database}?", 1)


async def _prepare(admin_url: str) -> None:
    admin = create_async_engine(admin_url)
    tables = [*ACCESS_TABLES, *KNOWLEDGE_TABLES, *JOB_TABLES, User.__table__, ActionReceipt.__table__,
              ActionResultBlob.__table__]  # 任务表：回合结束时给待判断的回合排任务（answer_check）
    async with admin.begin() as conn:
        await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
        await enable_row_level_security(conn, (*ACCESS_RLS_TABLES, *KNOWLEDGE_RLS_TABLES, *JOB_RLS_TABLES))
        for statement in (f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}",
                          f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}",
                          f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}"):
            await conn.execute(text(statement))
        for uid, email, status in USERS:
            await conn.execute(text('INSERT INTO "user" (id, email, name, status) VALUES (:i, :e, :i, :s)'),
                               {"i": uid, "e": email, "s": status})
    await admin.dispose()


@pytest.fixture
async def db(database_url):
    name = "kw_knowledge_" + uuid.uuid4().hex[:12]
    server = create_async_engine(database_url, isolation_level="AUTOCOMMIT")
    async with server.connect() as conn:
        if not await conn.scalar(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": APP_ROLE}):
            await conn.execute(text(f"CREATE ROLE {APP_ROLE} LOGIN NOSUPERUSER NOBYPASSRLS"))
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    await _prepare(_swap(database_url, user="postgres", database=name))
    yield Db(app_url=_swap(database_url, user=APP_ROLE, database=name))
    async with server.connect() as conn:
        await conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    await server.dispose()


def make_stub() -> KnowFlowStub:
    """alice 能读 ds1、ds2；bob 只能读 ds1；carol 一个都读不了；erin 在 KnowFlow 没有账号。"""
    return KnowFlowStub(
        users=[StubUser("kf-a", "alice@corp.com", {"ds1", "ds2"}), StubUser("kf-b", "bob@corp.com", {"ds1"}),
               StubUser("kf-c", "carol@corp.com", set())],
        api_keys={"ragflow-alicekey": "alice@corp.com", "ragflow-bobkey": "bob@corp.com"},
        chunks={"ds1": [chunk("ds1", "contract", 1, "付款期限 30 天"), chunk("ds1", "contract", 2)],
                "ds2": [chunk("ds2", "secret", 1, "机密条款")]},
        documents={"contract": [chunk("ds1", "contract", i, "甲" * 40) for i in range(1, 4)]},
    )


@dataclass
class Env:
    access: AccessService
    stub: KnowFlowStub
    knowledge: object
    sessionmaker: object


@pytest.fixture
async def env(db):
    engine = make_engine(db.app_url)
    sessionmaker = make_sessionmaker(engine)
    access = AccessService(sessionmaker)
    assert await access.reload()
    await access.create_workspace(workspace_id="ws1", name="空间一", owner_user_id="alice")
    for user, role in (("bob", "admin"), ("carol", "editor"), ("dave", "editor"), ("erin", "editor")):
        await access.add_member("alice", "ws1", user, role)
    stub = make_stub()
    knowledge = build_knowledge(MASTER, sessionmaker, access, client=httpx.AsyncClient(transport=stub.transport()))
    yield Env(access=access, stub=stub, knowledge=knowledge, sessionmaker=sessionmaker)
    await knowledge.aclose()
    await engine.dispose()


def flat(payload: dict) -> list[dict]:
    """knowledge_search 的结果按文档分组；断言按片段写的测试摊平成一条一条（带上所在文档的字段）。"""
    return [{**{k: v for k, v in doc.items() if k != "results"}, **piece}
            for doc in payload["documents"] for piece in doc["results"]]
