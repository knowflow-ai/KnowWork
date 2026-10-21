"""行级安全：普通角色连接时，漏写 workspace 条件也读不到别的空间；不设置变量时什么都读不到。"""

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from knowwork_server.access.models import UserGroup, WorkspaceMember
from knowwork_server.access.rls import row_level_security_statements, set_workspace
from knowwork_server.db.base import make_engine, make_sessionmaker

pytestmark = pytest.mark.anyio


async def _two_workspaces(service):
    await service.create_workspace(workspace_id="wa", name="A", owner_user_id="alice")
    await service.create_workspace(workspace_id="wb", name="B", owner_user_id="bob")
    await service.create_group("alice", "wa", "A 组")
    await service.create_group("bob", "wb", "B 组")


async def test_app_role_is_not_superuser(access_db):
    engine = make_engine(access_db.app_url)
    async with engine.connect() as conn:
        row = (await conn.execute(text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"))).one()
    await engine.dispose()
    assert row == (False, False)


async def test_query_without_workspace_filter_sees_only_current_workspace(access_db, service):
    await _two_workspaces(service)
    engine = make_engine(access_db.app_url)
    async with make_sessionmaker(engine)() as session, session.begin():
        await set_workspace(session, "wa")
        names = (await session.scalars(select(UserGroup.name))).all()  # 故意不带 workspace 条件
        members = (await session.scalars(select(WorkspaceMember.user_id))).all()
    await engine.dispose()
    assert names == ["A 组"]
    assert members == ["alice"]


async def test_without_setting_nothing_is_visible(access_db, service):
    await _two_workspaces(service)
    engine = make_engine(access_db.app_url)
    async with make_sessionmaker(engine)() as session:
        assert (await session.scalars(select(UserGroup))).all() == []
        assert (await session.scalars(select(WorkspaceMember))).all() == []
    await engine.dispose()


async def test_setting_is_transaction_local(access_db, service):
    await _two_workspaces(service)
    engine = make_engine(access_db.app_url)
    async with engine.connect() as conn:
        async with conn.begin():
            await set_workspace(conn, "wa")
            assert len((await conn.execute(select(UserGroup.id))).all()) == 1
        assert (await conn.execute(select(UserGroup.id))).all() == []
    await engine.dispose()


async def test_cannot_write_into_another_workspace(access_db, service):
    await _two_workspaces(service)
    engine = make_engine(access_db.app_url)
    with pytest.raises(DBAPIError):
        async with make_sessionmaker(engine)() as session, session.begin():
            await set_workspace(session, "wa")
            session.add(UserGroup(id="sneaky", workspace_id="wb", name="偷渡"))
    await engine.dispose()


async def test_superuser_bypasses_rls_which_is_why_tests_use_app_role(access_db, service):
    await _two_workspaces(service)
    engine = make_engine(access_db.admin_url)
    async with make_sessionmaker(engine)() as session:
        assert len((await session.scalars(select(UserGroup))).all()) == 2
    await engine.dispose()


def test_statements_reject_bad_identifiers():
    assert any("FORCE ROW LEVEL SECURITY" in s for s in row_level_security_statements(["t1"]))
    with pytest.raises(ValueError):
        row_level_security_statements(['x"; DROP TABLE y; --'])


async def test_set_workspace_requires_value(access_db):
    engine = make_engine(access_db.app_url)
    async with make_sessionmaker(engine)() as session:
        with pytest.raises(ValueError):
            await set_workspace(session, "")
    await engine.dispose()
