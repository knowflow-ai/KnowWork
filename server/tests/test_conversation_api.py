"""会话公开接口：令牌比较不因非 ASCII 崩溃、宿主错误统一映射且不外泄、审批与提问 id 校验。"""

import httpx
import pytest
from fastapi import FastAPI

from knowwork_server.conversations.api import bearer_matches, build_router
from knowwork_server.conversations.feedback import FeedbackStore
from knowwork_server.conversations.listeners import Broadcaster
from knowwork_server.conversations.pump import EventPump
from knowwork_server.conversations.replay import EventFeed
from knowwork_server.conversations.service import ConversationService
from knowwork_server.conversations.writer import BatchWriter
from knowwork_server.kernel.contract import KernelError, SessionBusy, SessionNotFound

from .fakes import FakeKernel, token_actor

CONV = "/api/workspaces/default/conversations"

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
    app.include_router(build_router(service, token_actor(TOKEN), FeedbackStore(sessionmaker)))
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
        response = await client.post(CONV, json={},
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
        response = await client.request(method, f"{CONV}/{conversation_id}/{path}", headers=AUTH)
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
        response = await client.post(CONV, json={}, headers=AUTH)
    assert response.status_code == status
    await pump.close()


async def test_decide_maps_errors_and_validates_id(sessionmaker, conversation_id):
    client, pump = api(Failing(KernelError("approval_not_found", "x", 404)), sessionmaker)
    async with client:
        missing = await client.post(f"{CONV}/{conversation_id}/approvals/a-1", json={"allow": True},
                                    headers=AUTH)
        bad_id = await client.post(f"{CONV}/{conversation_id}/approvals/a%3Fb", json={"allow": True},
                                   headers=AUTH)
    assert missing.status_code == 404
    assert bad_id.status_code == 422
    await pump.close()


async def test_approvals_listing_hides_contract_version(sessionmaker, conversation_id):
    client, pump = api(FakeKernel(), sessionmaker)
    async with client:
        response = await client.get(f"{CONV}/{conversation_id}/approvals", headers=AUTH)
    assert response.json() == [{"id": "a1", "tool_name": "t", "call_id": None, "reason": None}]
    await pump.close()


ANSWER = {"answers": [{"id": "fmt", "selected": ["PDF"]}]}


async def test_answer_reaches_the_host(sessionmaker, conversation_id):
    kernel = FakeKernel()
    client, pump = api(kernel, sessionmaker)
    async with client:
        response = await client.post(f"{CONV}/{conversation_id}/questions/call_1",
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
        response = await client.post(f"{CONV}/{conversation_id}/questions/call_1", json=ANSWER,
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
        response = await client.post(f"{CONV}/{conversation_id}/questions/{path}", json=body, headers=AUTH)
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
        response = await client.post(f"{CONV}/{conversation_id}/questions/call_1", json=ANSWER,
                                     headers=AUTH)
    assert response.status_code == 404
    assert kernel.answered == [], "只有会话发起人能回答"
    await pump.close()


async def test_questions_listing_hides_contract_version(sessionmaker, conversation_id):
    client, pump = api(FakeKernel(), sessionmaker)
    async with client:
        response = await client.get(f"{CONV}/{conversation_id}/questions", headers=AUTH)
    assert response.json() == [{"id": "call_1", "state": "open", "questions": [{
        "id": "fmt", "question": "格式？", "header": None, "detail": None, "multi_select": False,
        "options": [{"label": "PDF", "description": None}]}]}]
    await pump.close()


async def test_feedback_on_a_run_can_be_recorded_changed_and_retracted(sessionmaker, conversation_id):
    """照 DSH 官方的消息反馈：赞或踩必填，分类（官方的固定分类）与说明可选；再评就改，撤回就删。"""
    client, pump = api(FakeKernel(), sessionmaker)
    url = f"{CONV}/{conversation_id}/feedback"
    async with client:
        assert (await client.get(url, headers=AUTH)).json() == {"items": []}
        liked = await client.put(f"{url}/run-1", json={"rating": "like"}, headers=AUTH)
        assert liked.json() == {"runId": "run-1", "rating": "like", "category": None, "note": None}
        changed = await client.put(f"{url}/run-1", json={"rating": "dislike", "category": "task-result",
                                                         "note": "  没答到点子上 "}, headers=AUTH)
        assert changed.json()["rating"] == "dislike" and changed.json()["note"] == "没答到点子上"
        assert [i["rating"] for i in (await client.get(url, headers=AUTH)).json()["items"]] == ["dislike"]
        assert (await client.put(f"{url}/run-1", json={"rating": "dislike", "category": "随便写"},
                                 headers=AUTH)).status_code == 422
        assert (await client.put(f"{url}/abc", json={"rating": "like"}, headers=AUTH)).status_code == 422
        assert (await client.put(f"{CONV}/missing/feedback/run-1", json={"rating": "like"},
                                 headers=AUTH)).status_code == 404
        assert (await client.delete(f"{url}/run-1", headers=AUTH)).status_code == 204
        assert (await client.get(url, headers=AUTH)).json() == {"items": []}
    await pump.close()


async def test_search_finds_my_conversations_by_title_or_question(sessionmaker):
    """侧栏的搜索：标题或每轮问的话里有就算，一个对话一条、带命中的那句问话；别人的不出来，% 不当通配符。"""
    from datetime import UTC, datetime

    from knowwork_server.conversations.models import Conversation, RunSummary

    now = datetime.now(UTC)
    async with sessionmaker() as session:
        session.add_all([
            Conversation(id="c_title", workspace_id="default", user_id="dev-user", title="差旅报销标准"),
            Conversation(id="c_turns", workspace_id="default", user_id="dev-user", title="随便聊聊"),
            Conversation(id="c_other", workspace_id="default", user_id="someone", title="差旅报销"),
            Conversation(id="c_pct", workspace_id="default", user_id="dev-user", title="增长 50% 的原因"),
        ])
        await session.flush()
        session.add_all([
            RunSummary(conversation_id="c_turns", turn=1, workspace_id="default", user_id="dev-user",
                       question="你好", status="completed", started_at=now),
            RunSummary(conversation_id="c_turns", turn=2, workspace_id="default", user_id="dev-user",
                       question="出差去上海的差旅报销能报多少，高铁一等座可以吗", status="completed",
                       started_at=now),
        ])
        await session.commit()
    http, _ = api(FakeKernel(), sessionmaker)
    async with http:
        found = (await http.get(f"{CONV}/search", params={"q": "差旅报销"}, headers=AUTH)).json()["items"]
        percent = (await http.get(f"{CONV}/search", params={"q": "50%"}, headers=AUTH)).json()["items"]
        wildcard = (await http.get(f"{CONV}/search", params={"q": "%"}, headers=AUTH)).json()["items"]
        empty = await http.get(f"{CONV}/search", params={"q": ""}, headers=AUTH)
    by_id = {item["id"]: item for item in found}
    assert set(by_id) == {"c_title", "c_turns"}, "别人的对话不出来"
    assert by_id["c_title"]["snippet"] == ""
    assert by_id["c_turns"]["snippet"] == "出差去上海的差旅报销能报多少，高铁一等座可以吗"
    assert [item["id"] for item in percent] == ["c_pct"] and [item["id"] for item in wildcard] == ["c_pct"]
    assert empty.status_code == 422


def test_search_snippet_keeps_a_little_context_around_the_hit():
    from knowwork_server.conversations.search import snippet

    long = "前面" * 30 + "差旅报销" + "后面" * 30
    cut = snippet(long, "差旅报销")
    assert cut.startswith("…") and cut.endswith("…") and "差旅报销" in cut and len(cut) < 70
    assert snippet("多个   空白\n换行 差旅", "差旅") == "多个 空白 换行 差旅"
