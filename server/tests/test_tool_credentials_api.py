"""工具令牌拿不到时的接口映射：签发失败 503 固定文案不泄露原因，成员身份被收回 403。"""

import httpx
import pytest
from fastapi import FastAPI

from knowwork_server.conversations.api import build_router
from knowwork_server.conversations.listeners import Broadcaster
from knowwork_server.conversations.pump import EventPump
from knowwork_server.conversations.replay import EventFeed
from knowwork_server.conversations.service import ConversationService
from knowwork_server.conversations.tool_credentials import ToolCredentials, require_membership
from knowwork_server.conversations.writer import BatchWriter
from knowwork_server.kernel.contract import SessionNotFound

from .fakes import FakeKernel, token_actor

pytestmark = pytest.mark.anyio

TOKEN = "e" * 48
AUTH = {"authorization": f"Bearer {TOKEN}"}


def api(kernel, sessionmaker, issue):
    writer, broadcaster = BatchWriter(sessionmaker), Broadcaster()
    pump = EventPump(kernel, sessionmaker, writer, broadcaster)
    feed = EventFeed(sessionmaker, writer, broadcaster, pump.produced_upto)
    service = ConversationService(kernel, sessionmaker, pump, feed, resume_retry_delays=(0.01,),
                                  tool_credentials=ToolCredentials(kernel, issue, interval=3600, call_timeout=1.0))
    app = FastAPI()
    app.include_router(build_router(service, token_actor(TOKEN, user_id="u1")))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://public"), pump


async def test_issue_failure_on_create_is_503_without_details(sessionmaker):
    async def broken(conversation):
        raise RuntimeError("signing key /etc/secret missing")

    client, pump = api(FakeKernel(), sessionmaker, broken)
    async with client:
        response = await client.post("/api/conversations", json={}, headers=AUTH)
    assert response.status_code == 503
    assert response.json()["detail"] == {"code": "tool_credentials_unavailable", "message": "工具服务暂时不可用，请稍后重试"}
    assert "secret" not in response.text
    await pump.close()


async def test_revoked_member_on_resume_is_403(sessionmaker):
    members = {"u1"}

    async def issue(conversation):
        return "tool-token-" + "x" * 16

    kernel = FakeKernel()
    client, pump = api(kernel, sessionmaker, require_membership(issue, lambda c: c.user_id in members))
    async with client:
        created = await client.post("/api/conversations", json={}, headers=AUTH)
        assert created.status_code == 201
        members.clear()
        kernel.send_effects = [SessionNotFound("session_not_found", "gone", 404)]
        response = await client.post(f"/api/conversations/{created.json()['id']}/messages", json={"text": "hi"},
                                     headers=AUTH)
    assert response.status_code == 403
    assert response.json()["detail"] == {"code": "not_a_member", "message": "你已不是本工作空间的成员"}
    await pump.close()


async def test_issue_failure_on_resume_is_503(sessionmaker):
    calls = []

    async def issue(conversation):
        calls.append(conversation.id)
        if len(calls) > 1:
            raise OSError("signer unreachable")
        return "tool-token-" + "x" * 16

    kernel = FakeKernel()
    client, pump = api(kernel, sessionmaker, issue)
    async with client:
        created = await client.post("/api/conversations", json={}, headers=AUTH)
        kernel.send_effects = [SessionNotFound("session_not_found", "gone", 404)]
        response = await client.post(f"/api/conversations/{created.json()['id']}/messages", json={"text": "hi"},
                                     headers=AUTH)
    assert response.status_code == 503
    assert "unreachable" not in response.text
    await pump.close()
