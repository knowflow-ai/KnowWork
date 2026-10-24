"""附件、产物与后台任务测试共用的数据库夹具：独立库，超级用户建表与开启行级安全，业务读写用普通角色（不绕过 RLS）。"""

import uuid
from dataclasses import dataclass

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from knowwork_server.access.rls import enable_row_level_security, set_workspace
from knowwork_server.artifacts import ARTIFACT_RLS_TABLES, ARTIFACT_TABLES
from knowwork_server.attachments import ATTACHMENT_RLS_TABLES, ATTACHMENT_TABLES
from knowwork_server.conversations.models import Conversation, RunEvent
from knowwork_server.conversations.service import ConversationNotFound
from knowwork_server.db.base import Base, make_engine, make_sessionmaker
from knowwork_server.jobs import JOB_RLS_TABLES, JOB_TABLES

APP_ROLE = "kw_files_app"


def _swap(url: str, *, user: str, database: str) -> str:
    return url.replace("postgres@", f"{user}@", 1).replace("/postgres?", f"/{database}?", 1)


async def _prepare(admin_url: str) -> None:
    admin = create_async_engine(admin_url)
    tables = [Conversation.__table__, RunEvent.__table__, *ATTACHMENT_TABLES, *ARTIFACT_TABLES, *JOB_TABLES]
    async with admin.begin() as conn:
        await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
        await enable_row_level_security(conn, ("conversation", "run_event", *ATTACHMENT_RLS_TABLES, *ARTIFACT_RLS_TABLES,
                                                *JOB_RLS_TABLES))
        for statement in (f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}",
                          f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}"):
            await conn.execute(text(statement))
    await admin.dispose()


class Lookup:
    """ConversationLookup 的测试实现：按（会话, 空间, 用户）查会话表，行级安全照常生效。"""

    def __init__(self, sessionmaker) -> None:
        self._sessionmaker = sessionmaker

    async def get(self, *, conversation_id, workspace_id, user_id):
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            row = await session.scalar(select(Conversation).where(
                Conversation.id == conversation_id, Conversation.user_id == user_id))
        if row is None:
            raise ConversationNotFound(conversation_id)
        return row


@dataclass(frozen=True)
class FilesDb:
    sessionmaker: object

    @property
    def lookup(self) -> Lookup:
        return Lookup(self.sessionmaker)

    async def delete_conversation(self, conversation: Conversation) -> None:
        async with self.sessionmaker() as session, session.begin():
            await set_workspace(session, conversation.workspace_id)
            await session.execute(delete(Conversation).where(Conversation.id == conversation.id))

    async def conversation(self, *, workspace_id: str = "ws1", user_id: str = "alice") -> Conversation:
        row = Conversation(id=f"c_{uuid.uuid4().hex}", workspace_id=workspace_id, user_id=user_id, title="")
        async with self.sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            session.add(row)
        return row


@pytest.fixture
async def files_db(database_url):
    name = "kw_files_" + uuid.uuid4().hex[:12]
    server = create_async_engine(database_url, isolation_level="AUTOCOMMIT")
    async with server.connect() as conn:
        if not await conn.scalar(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": APP_ROLE}):
            await conn.execute(text(f"CREATE ROLE {APP_ROLE} LOGIN NOSUPERUSER NOBYPASSRLS"))
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    await _prepare(_swap(database_url, user="postgres", database=name))
    engine = make_engine(_swap(database_url, user=APP_ROLE, database=name))
    yield FilesDb(sessionmaker=make_sessionmaker(engine))
    await engine.dispose()
    async with server.connect() as conn:
        await conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    await server.dispose()
