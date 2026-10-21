"""引用链路：按会话取出处（只有会话本人能看）、知识工具带引用规则、回合结束时校验回答里的编号。"""

import logging

import httpx
import pytest
from fastapi import FastAPI

from knowwork_server.conversations.api import Actor
from knowwork_server.conversations.models import Conversation
from knowwork_server.conversations.service import ConversationNotFound
from knowwork_server.knowledge.citation_api import EXCERPT_LIMIT, build_router
from knowwork_server.knowledge.citation_observer import CitationObserver
from knowwork_server.knowledge.models import Box, Evidence, Locator
from knowwork_server.knowledge.tools import CITATION_GUIDANCE

pytestmark = pytest.mark.anyio


class Lookup:
    async def get(self, *, conversation_id, workspace_id, user_id):
        if conversation_id != "c1" or user_id != "alice":
            raise ConversationNotFound(conversation_id)
        return Conversation(id="c1", workspace_id=workspace_id, user_id=user_id, title="")


def actor_for(user):
    async def dependency():
        return Actor(user_id=user, workspace_id="ws1")

    return dependency


def client(env, user="alice"):
    app = FastAPI()
    app.include_router(build_router(env.knowledge.citations, Lookup(), actor_for(user)))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://kw")


def evidence(i, text="付款期限 30 天", page=3):
    return Evidence(source_id="src1", doc_ref=f"doc{i}", span_ref=f"span{i}", title=f"合同{i}.pdf", text=text,
                    locator=Locator(page=page, boxes=(Box(page=page, x0=1, x1=2, top=3, bottom=4),)),
                    open_url=f"http://kf.local/document/doc{i}?page={page}")


async def test_lists_the_sources_of_a_conversation_for_its_owner(env):
    await env.knowledge.citations.register("ws1", "c1", [evidence(1), evidence(2, text="甲" * 2000, page=7)])
    async with client(env) as http:
        body = (await http.get("/api/conversations/c1/citations")).json()
    first, second = body["items"]
    assert first == {"number": 1, "title": "合同1.pdf", "excerpt": "付款期限 30 天", "page": 3,
                     "openUrl": "http://kf.local/document/doc1?page=3", "sourceId": "src1", "docRef": "doc1"}
    assert second["number"] == 2 and len(second["excerpt"]) <= EXCERPT_LIMIT + 1 and second["excerpt"].endswith("…")
    assert body["truncated"] is False


async def test_other_users_and_unknown_conversations_get_404(env):
    await env.knowledge.citations.register("ws1", "c1", [evidence(1)])
    async with client(env, user="bob") as http:
        assert (await http.get("/api/conversations/c1/citations")).status_code == 404
    async with client(env) as http:
        assert (await http.get("/api/conversations/nope/citations")).status_code == 404


async def test_a_conversation_without_evidence_has_no_sources(env):
    async with client(env) as http:
        assert (await http.get("/api/conversations/c1/citations")).json() == {"items": [], "truncated": False,
                                                                              "dropped": 0}


async def test_knowledge_tools_carry_the_citation_rule(env):
    for tool in env.knowledge.tools:
        assert tool.spec.guidance == CITATION_GUIDANCE
    assert "[1]" in CITATION_GUIDANCE and "不要编造" in CITATION_GUIDANCE


def turn_end(turn, kind="completed"):
    return {"type": "turn/end", "data": {"turn": turn, "reason": {"kind": kind}}}


def answer(turn, text):
    return {"type": "assistant/message", "data": {"turn": turn, "step": 1,
                                                  "message": {"content": [{"type": "text", "text": text}]}}}


async def test_answer_check_flags_numbers_that_no_tool_returned(env, caplog):
    await env.knowledge.citations.register("ws1", "c1", [evidence(1)])
    observer = CitationObserver(env.knowledge.citations)
    caplog.set_level(logging.WARNING)
    observer("c1", "ws1", "session/event", answer(2, "付款期限 30 天[1]，违约金 5%[5]。"))
    observer("c1", "ws1", "session/event", turn_end(2))
    await observer.drain()
    assert observer.last_check("c1").removed == (5,)
    assert any("[5]" in record.message for record in caplog.records)


async def test_answer_check_skips_aborted_turns_and_answers_without_citations(env):
    observer = CitationObserver(env.knowledge.citations)
    observer("c1", "ws1", "session/event", answer(1, "没有引用的回答"))
    observer("c1", "ws1", "session/event", turn_end(1, "aborted"))
    await observer.drain()
    assert observer.last_check("c1") is None
