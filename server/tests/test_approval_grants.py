"""「本会话内同类操作不再询问」：授权的记与查、按运行配置版本失效，以及宿主来问的内部接口。"""

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.dialects.postgresql import insert

import knowwork_server.approvals.models  # noqa: F401  注册表结构
from knowwork_server.access.models import Workspace
from knowwork_server.approvals import SessionGrants
from knowwork_server.tools import build_tool_service
from knowwork_server.tools.api import PREFIX, build_internal_router
from knowwork_server.tools.registry import builtin_registry

pytestmark = pytest.mark.anyio
MASTER = "grant-test-master-key-".ljust(40, "g")
TOOL = "mcp__deepwiki__read_wiki_structure"


@pytest.fixture
async def grants(sessionmaker, conversation_id):
    async with sessionmaker() as session, session.begin():
        await session.execute(insert(Workspace).values(id="default", name="默认空间").on_conflict_do_nothing())
    versions = {"current": "p1"}

    async def version_of(ref):
        assert ref.id == conversation_id
        return versions["current"]

    return SessionGrants(sessionmaker, version_of), versions, conversation_id


async def test_grant_covers_only_same_user_tool_and_profile_version(grants):
    store, versions, cid = grants
    assert not await store.covers("default", cid, "dev-user", TOOL)
    assert await store.remember("default", cid, "dev-user", TOOL)
    assert await store.remember("default", cid, "dev-user", TOOL), "同一范围重复记不报错"
    assert await store.covers("default", cid, "dev-user", TOOL)
    assert not await store.covers("default", cid, "someone-else", TOOL), "授权只属于批准的人"
    assert not await store.covers("default", cid, "dev-user", "mcp__deepwiki__ask_wiki_question"), "只认同一个工具"
    versions["current"] = "p2"
    assert not await store.covers("default", cid, "dev-user", TOOL), "运行配置变了（例如管理员改了风险等级），重新问"


async def test_unbound_conversation_records_nothing(grants):
    store, versions, cid = grants
    versions["current"] = None
    assert not await store.remember("default", cid, "dev-user", TOOL)
    assert not await store.covers("default", cid, "dev-user", TOOL)


async def test_host_asks_with_the_session_token(sessionmaker):
    asked = []

    async def check(workspace_id, conversation_id, user_id, tool_name):
        asked.append((workspace_id, conversation_id, user_id, tool_name))
        return tool_name == TOOL

    service = build_tool_service(MASTER, sessionmaker, registry=builtin_registry())
    app = FastAPI()
    app.include_router(build_internal_router(service, grants=check))
    token = await service.issue_session_token("ws1", "conv-1", user_id="u1")
    auth = {"authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://internal") as client:
        hit = await client.post(f"{PREFIX}/approval-grant", json={"toolName": TOOL}, headers=auth)
        miss = await client.post(f"{PREFIX}/approval-grant", json={"toolName": "kw_note"}, headers=auth)
        bad = await client.post(f"{PREFIX}/approval-grant", json={"toolName": "a b"}, headers=auth)
        anonymous = await client.post(f"{PREFIX}/approval-grant", json={"toolName": TOOL})
    assert (hit.json(), miss.json()) == ({"granted": True}, {"granted": False})
    assert (bad.status_code, anonymous.status_code) == (400, 401)
    assert asked[0] == ("ws1", "conv-1", "u1", TOOL), "对话与用户取自令牌，不接受宿主自报"
