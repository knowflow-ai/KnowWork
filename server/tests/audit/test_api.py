"""审计查询接口：只有空间 owner/admin 能看，按时间、操作者、动作、结果过滤，键集分页，行级安全隔离空间。"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI, Request

from knowwork_server.audit.api import build_router
from knowwork_server.audit.events import AuditEvent
from knowwork_server.audit.query import AuditQuery, decode_cursor
from knowwork_server.audit.writer import AuditWriter
from knowwork_server.auth.principal import Principal

pytestmark = pytest.mark.anyio
T0 = datetime(2026, 10, 1, tzinfo=UTC)
URL = "/api/workspaces/ws1/audit-logs"


async def seed(sessionmaker) -> None:
    writer = AuditWriter(sessionmaker)
    writer.start()
    rows = [("human", "alice", "member.add", "success"), ("human", "bob", "api_key.create", "success"),
            ("human", None, "auth.login", "denied"), ("agent", "alice", "tool.execute", "failed"),
            ("human", "bob", "api_key.revoke", "success")]
    for i, (actor_type, actor_id, action, result) in enumerate(rows):
        writer.put(AuditEvent(workspace_id="ws1", actor_type=actor_type, actor_id=actor_id, action=action,
                              result=result, occurred_at=T0 + timedelta(hours=i), detail={"n": i}))
    writer.put(AuditEvent(workspace_id="ws2", actor_type="human", actor_id="zed", action="member.add",
                          result="success", occurred_at=T0))
    await writer.flush()
    await writer.close()


@pytest.fixture
async def client(app_sessionmaker, access):
    await seed(app_sessionmaker)

    async def principal(request: Request) -> Principal:
        return Principal(user_id=request.headers.get("x-user", "nobody"), auth_method="session")

    app = FastAPI()
    app.include_router(build_router(AuditQuery(app_sessionmaker), access, principal))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://kw") as http:
        yield http


async def test_owner_and_admin_can_read_newest_first(client):
    for user in ("alice", "bob"):
        response = await client.get(URL, headers={"x-user": user})
        assert response.status_code == 200
        body = response.json()
        assert [item["detail"]["n"] for item in body["items"]] == [4, 3, 2, 1, 0]
        assert body["next"] is None
    item = body["items"][1]
    assert (item["actorType"], item["actorId"], item["action"], item["result"]) == ("agent", "alice", "tool.execute",
                                                                                    "failed")


@pytest.mark.parametrize("user", ["carol", "zed", "nobody"])
async def test_others_are_forbidden(client, user):
    assert (await client.get(URL, headers={"x-user": user})).status_code == 403


async def test_unhealthy_access_engine_fails_closed(client, access, monkeypatch):
    monkeypatch.setattr(type(access.engine), "healthy", property(lambda self: False))
    assert (await client.get(URL, headers={"x-user": "alice"})).status_code == 503


@pytest.mark.parametrize("params,expected", [
    ({"actorId": "bob"}, [4, 1]),
    ({"actorType": "agent"}, [3]),
    ({"result": "denied"}, [2]),
    ({"action": "api_key.*"}, [4, 1]),
    ({"action": "member.add"}, [0]),
    ({"since": (T0 + timedelta(hours=1)).isoformat(), "until": (T0 + timedelta(hours=3)).isoformat()}, [2, 1]),
])
async def test_filters(client, params, expected):
    response = await client.get(URL, params=params, headers={"x-user": "alice"})
    assert [item["detail"]["n"] for item in response.json()["items"]] == expected


async def test_keyset_pagination(client):
    seen, cursor = [], None
    while True:
        params = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        body = (await client.get(URL, params=params, headers={"x-user": "alice"})).json()
        seen += [item["detail"]["n"] for item in body["items"]]
        cursor = body["next"]
        if cursor is None:
            break
    assert seen == [4, 3, 2, 1, 0]


@pytest.mark.parametrize("params", [{"cursor": "not-a-cursor"}, {"since": "2026-10-01T00:00:00"},
                                    {"action": "DROP TABLE"}, {"result": "ok"}, {"limit": 0}, {"actorType": "bot"}])
async def test_invalid_input_is_rejected(client, params):
    assert (await client.get(URL, params=params, headers={"x-user": "alice"})).status_code == 422


def test_cursor_round_trip_rejects_naive_or_empty():
    import base64

    with pytest.raises(ValueError):
        decode_cursor(base64.urlsafe_b64encode(b"2026-10-01T00:00:00|al_1").decode())
    with pytest.raises(ValueError):
        decode_cursor(base64.urlsafe_b64encode(b"2026-10-01T00:00:00+00:00|").decode())
