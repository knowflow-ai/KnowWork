"""会话公开接口：令牌比较不因非 ASCII 崩溃、宿主错误统一映射且不外泄、审批与提问 id 校验。"""

import httpx
import pytest
from fastapi import FastAPI

from knowwork_server.conversations.api import bearer_matches, build_router
from knowwork_server.conversations.listeners import Broadcaster
from knowwork_server.conversations.pump import EventPump
from knowwork_server.conversations.replay import EventFeed
from knowwork_server.conversations.service import ConversationService
from knowwork_server.conversations.writer import BatchWriter
from knowwork_server.kernel.contract import KernelError, SessionBusy, SessionNotFound

from .fakes import FakeKernel, token_actor

pytestmark = pytest.mark.anyio

TOKEN = "d" * 48
AUTH = {"authorization": f"Bearer {TOKEN}"}


class Failing(FakeKernel):
    def __init__(self, error):
        super().__init__()
        self.error = error

    async def cancel(self, session_id):
        raise self.error

    async def pending_approvals(self, session_id):
        raise self.error

    async def decide_approval(self, session_id, approval_id, allow):
        raise self.error

    async def pending_questions(self, session_id):
        raise self.error

    async def answer_question(self, session_id, question_id, answer):
        raise self.error


def api(kernel, sessionmaker):
    writer, broadcaster = BatchWriter(sessionmaker), Broadcaster()
    pump = EventPump(kernel, sessionmaker, writer, broadcaster)
    service = ConversationService(kernel, sessionmaker, pump, EventFeed(sessionmaker, writer, broadcaster,
                                                                        pump.produced_upto))
    app = FastAPI()
    app.include_router(build_router(service, token_actor(TOKEN)))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://public"), pump


@pytest.mark.parametrize(("header", "ok"), [
    (f"Bearer {TOKEN}", True), (None, False), ("", False), (TOKEN, False), (f"bearer {TOKEN}", False),
    (f"Bearer {TOKEN}x", False), ("Bearer éé", False), ("Bearer 令牌", False), ("Bearer \udcff", False),
])
def test_bearer_matches_never_raises(header, ok):
    assert bearer_matches(header, TOKEN) is ok


async def test_non_ascii_token_is_401_not_500(sessionmaker):
    client, pump = api(FakeKernel(), sessionmaker)
    async with client:
        response = await client.post("/api/conversations", json={},
                                     headers={"authorization": "Bearer été".encode("latin-1")})
    assert response.status_code == 401
    await pump.close()


@pytest.mark.parametrize(("error", "status"), [
    (SessionNotFound("session_not_found", "宿主内部细节 /var/lib/x", 404), 404),
    (KernelError("internal", "宿主内部细节 /var/lib/x", 500), 502),
    (httpx.ConnectError("connect to 10.0.0.4 failed"), 502),
])
@pytest.mark.parametrize(("method", "path"), [("POST", "cancel"), ("GET", "approvals")])
async def test_cancel_and_approvals_map_errors(sessionmaker, conversation_id, error, status, method, path):
    client, pump = api(Failing(error), sessionmaker)
    async with client:
        response = await client.request(method, f"/api/conversations/{conversation_id}/{path}", headers=AUTH)
    assert response.status_code == status
    assert "/var/lib" not in response.text and "10.0.0.4" not in response.text
    await pump.close()


@pytest.mark.parametrize(("error", "status"), [
    (SessionBusy("session_busy", "x", 409), 409),
    (KernelError("internal", "x", 500), 502),
    (httpx.ReadTimeout("slow"), 502),
])
async def test_create_maps_errors(sessionmaker, error, status):
    kernel = FakeKernel()
    kernel.open_effects = [error]
    client, pump = api(kernel, sessionmaker)
    async with client:
        response = await client.post("/api/conversations", json={}, headers=AUTH)
    assert response.status_code == status
    await pump.close()


async def test_decide_maps_errors_and_validates_id(sessionmaker, conversation_id):
    client, pump = api(Failing(KernelError("approval_not_found", "x", 404)), sessionmaker)
    async with client:
        missing = await client.post(f"/api/conversations/{conversation_id}/approvals/a-1", json={"allow": True},
                                    headers=AUTH)
        bad_id = await client.post(f"/api/conversations/{conversation_id}/approvals/a%3Fb", json={"allow": True},
                                   headers=AUTH)
    assert missing.status_code == 404
    assert bad_id.status_code == 422
    await pump.close()


async def test_approvals_listing_hides_contract_version(sessionmaker, conversation_id):
    client, pump = api(FakeKernel(), sessionmaker)
    async with client:
        response = await client.get(f"/api/conversations/{conversation_id}/approvals", headers=AUTH)
    assert response.json() == [{"id": "a1", "tool_name": "t", "call_id": None, "reason": None}]
    await pump.close()


ANSWER = {"answers": [{"id": "fmt", "selected": ["PDF"]}]}


async def test_answer_reaches_the_host(sessionmaker, conversation_id):
    kernel = FakeKernel()
    client, pump = api(kernel, sessionmaker)
    async with client:
        response = await client.post(f"/api/conversations/{conversation_id}/questions/call_1",
                                     json={"answers": [{"id": "fmt", "selected": ["PDF"], "custom": "按月"}]},
                                     headers=AUTH)
    assert response.json() == {"questionId": "call_1"}
    [(session_id, question_id, answer)] = kernel.answered
    assert (session_id, question_id) == (conversation_id, "call_1")
    assert [(a.id, a.selected, a.custom) for a in answer.answers] == [("fmt", ("PDF",), "按月")]
    await pump.close()


@pytest.mark.parametrize(("error", "status", "code"), [
    (KernelError("question_not_found", "宿主内部细节 /var/lib/x", 404), 404, "question_not_found"),
    (KernelError("invalid_answer", "没有选项「Word」", 400), 400, "invalid_answer"),
    (KernelError("reply_queued", "x", 409), 409, "reply_queued"),
    (SessionNotFound("session_not_found", "宿主内部细节 /var/lib/x", 404), 404, "session_not_running"),
    (KernelError("internal", "宿主内部细节 /var/lib/x", 500), 502, "runtime_unavailable"),
    (httpx.ConnectError("connect to 10.0.0.4 failed"), 502, "runtime_unavailable"),
])
async def test_answer_maps_host_errors(sessionmaker, conversation_id, error, status, code):
    client, pump = api(Failing(error), sessionmaker)
    async with client:
        response = await client.post(f"/api/conversations/{conversation_id}/questions/call_1", json=ANSWER,
                                     headers=AUTH)
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code
    assert "/var/lib" not in response.text and "10.0.0.4" not in response.text and "Word" not in response.text
    await pump.close()


@pytest.mark.parametrize(("path", "body"), [
    ("call%3F1", ANSWER),
    ("call_1", {"answers": []}),
    ("call_1", {"answers": [{"id": "", "selected": []}]}),
    ("call_1", {"answers": [{"id": "fmt", "selected": [""]}]}),
    ("call_1", {"answers": [{"id": "fmt", "custom": "x" * 4001}]}),
    ("call_1", {}),
])
async def test_answer_validates_input(sessionmaker, conversation_id, path, body):
    kernel = FakeKernel()
    client, pump = api(kernel, sessionmaker)
    async with client:
        response = await client.post(f"/api/conversations/{conversation_id}/questions/{path}", json=body, headers=AUTH)
    assert response.status_code == 422
    assert kernel.answered == []
    await pump.close()


async def test_answer_to_someone_elses_conversation_is_404(sessionmaker, conversation_id):
    kernel = FakeKernel()
    writer, broadcaster = BatchWriter(sessionmaker), Broadcaster()
    pump = EventPump(kernel, sessionmaker, writer, broadcaster)
    service = ConversationService(kernel, sessionmaker, pump, EventFeed(sessionmaker, writer, broadcaster,
                                                                        pump.produced_upto))
    app = FastAPI()
    app.include_router(build_router(service, token_actor(TOKEN, user_id="someone-else")))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://public") as client:
        response = await client.post(f"/api/conversations/{conversation_id}/questions/call_1", json=ANSWER,
                                     headers=AUTH)
    assert response.status_code == 404
    assert kernel.answered == [], "只有会话发起人能回答"
    await pump.close()


async def test_questions_listing_hides_contract_version(sessionmaker, conversation_id):
    client, pump = api(FakeKernel(), sessionmaker)
    async with client:
        response = await client.get(f"/api/conversations/{conversation_id}/questions", headers=AUTH)
    assert response.json() == [{"id": "call_1", "state": "open", "questions": [{
        "id": "fmt", "question": "格式？", "header": None, "detail": None, "multi_select": False,
        "options": [{"label": "PDF", "description": None}]}]}]
    await pump.close()
