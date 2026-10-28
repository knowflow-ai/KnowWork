"""引用链路：按会话取出处（只有会话本人能看）、知识工具带引用规则、回合结束时校验回答里的编号。"""

import logging

import httpx
import pytest
from fastapi import FastAPI

from knowwork_server.conversations.api import Actor
from knowwork_server.conversations.models import Conversation
from knowwork_server.conversations.service import ConversationNotFound
from knowwork_server.knowledge.citation_api import EXCERPT_LIMIT, build_router, citation_view
from knowwork_server.knowledge.citation_observer import CitationObserver
from knowwork_server.knowledge.links import LinkSigner
from knowwork_server.knowledge.models import Box, Evidence, EvidenceImage, Locator
from knowwork_server.knowledge.tools import CITATION_GUIDANCE

CONV = "/api/workspaces/default/conversations"

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
                    locator=Locator(type="pdf", page=page, boxes=(Box(page=page, x0=0.1, y0=0.2, x1=0.5, y1=0.3),),
                                    quote=text[:20], source_hash="sha256:abc"),
                    open_url=f"http://kf.local/document/doc{i}?page={page}")


async def test_lists_the_sources_of_a_conversation_for_its_owner(env):
    await env.knowledge.citations.register("ws1", "c1", [evidence(1), evidence(2, text="甲" * 2000, page=7)])
    async with client(env) as http:
        body = (await http.get(f"{CONV}/c1/citations")).json()
    first, second = body["items"]
    assert first == {"number": 1, "title": "合同1.pdf", "excerpt": "付款期限 30 天", "page": 3,
                     "openUrl": "http://kf.local/document/doc1?page=3", "sourceId": "src1", "docRef": "doc1",
                     # 预览里高亮用：页码、框、引用原文、原文件版本
                     "locator": {"type": "pdf", "page": 3, "boxes": [{"page": 3, "x0": 0.1, "y0": 0.2, "x1": 0.5,
                                                                      "y1": 0.3}],
                                 "quote": "付款期限 30 天", "source_hash": "sha256:abc"},
                     "locators": [], "images": []}
    assert second["number"] == 2 and len(second["excerpt"]) <= EXCERPT_LIMIT + 1 and second["excerpt"].endswith("…")
    assert body["truncated"] is False


async def test_images_come_with_signed_urls_for_the_answer(env):
    """回答里的 [IMG:n:j] 按出处里的图片换成图：<img> 带不了请求头，取图地址要签名；站外地址原样给。"""
    pictured = evidence(1).model_copy(update={"images": (
        EvidenceImage(ref="d1", caption="付款流程图", url="/api/workspaces/ws1/library/bases/kb1/images/d1"),
        EvidenceImage(ref="d2", caption="没有地址的图"))})
    await env.knowledge.citations.register("ws1", "c1", [pictured])
    app = FastAPI()
    app.include_router(build_router(env.knowledge.citations, Lookup(), actor_for("alice"), LinkSigner(b"k" * 32)))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://kw") as http:
        [item] = (await http.get(f"{CONV}/c1/citations")).json()["items"]
    [image] = item["images"]
    assert image["caption"] == "付款流程图" and image["url"].startswith("/api/workspaces/ws1/library/bases/kb1/images/d1?")
    assert "uid=alice" in image["url"] and "sig=" in image["url"]


async def test_other_users_and_unknown_conversations_get_404(env):
    await env.knowledge.citations.register("ws1", "c1", [evidence(1)])
    async with client(env, user="bob") as http:
        assert (await http.get(f"{CONV}/c1/citations")).status_code == 404
    async with client(env) as http:
        assert (await http.get(f"{CONV}/nope/citations")).status_code == 404


async def test_a_conversation_without_evidence_has_no_sources(env):
    async with client(env) as http:
        assert (await http.get(f"{CONV}/c1/citations")).json() == {"items": [], "truncated": False,
                                                                              "dropped": 0}


async def test_knowledge_tools_carry_the_citation_rule(env):
    """产出可引用证据的检索、阅读带引用规则；只列知识库与字段的 knowledge_bases、只列文档的 knowledge_documents 不带。
    没有让智能体自报缺口的工具：答上没有由回合结束后的判断来定（answer_check）。"""
    guidance = {tool.spec.name: tool.spec.guidance for tool in env.knowledge.tools}
    assert guidance == {"knowledge_search": CITATION_GUIDANCE, "knowledge_read": CITATION_GUIDANCE,
                        "knowledge_bases": None, "knowledge_documents": None}
    assert "[1]" in CITATION_GUIDANCE and "不要编造" in CITATION_GUIDANCE
    assert "照样写当时那条结果的 ref" in CITATION_GUIDANCE and "不要在回答末尾另写脚注" in CITATION_GUIDANCE, \
        "沿用前一轮的结果时不自己从 1 编脚注（实测编号落到了别的文档上）"


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


async def test_stored_citations_keep_locator_images_and_version_note(env):
    """引用目录落库后原样取回：新版定位（类型、比例框、引用原文、版本哈希）、图片列表、版本提示。"""
    original = evidence(1).model_copy(update={
        "images": (EvidenceImage(ref="img1", caption="付款流程图", url="/api/x/img1"),),
        "version_note": "这份文档已有更新版本"})
    await env.knowledge.citations.register("ws1", "c1", [original])
    [entry] = (await env.knowledge.citations.load("ws1", "c1")).entries
    assert (entry.evidence.locator, entry.evidence.images, entry.evidence.version_note) == (
        original.locator, original.images, original.version_note)
    assert citation_view(entry)["versionNote"] == "这份文档已有更新版本", "引用卡片照原样显示"


def block(page, quote, y=0.2):
    return Locator(type="pdf", page=page, boxes=(Box(page=page, x0=0.1, y0=y, x1=0.9, y1=y + 0.05),), quote=quote,
                   source_hash="sha256:abc")


async def test_each_block_of_the_passage_comes_with_its_place(env):
    """片段补过相邻块、合并过相邻命中后，一条引用横跨好几块；每个版面块的位置与原文都带上，
    前端按回答里引用的那句话挑最像的一块高亮（实测引用补进来的「可动部位不可把手指伸入」，却框在命中块的「说明」上）。"""
    blocks = (block(11, "说明"), block(11, "注意 可动部位不可把手指伸入。", 0.5))
    stored = evidence(1).model_copy(update={"locators": blocks})
    await env.knowledge.citations.register("ws1", "c1", [stored, evidence(2)])
    [first, second] = (await env.knowledge.citations.load("ws1", "c1")).entries
    assert first.evidence.locators == blocks and second.evidence.locators == ()
    async with client(env) as http:
        body = (await http.get(f"{CONV}/c1/citations")).json()
    assert [b["quote"] for b in body["items"][0]["locators"]] == ["说明", "注意 可动部位不可把手指伸入。"]
    assert body["items"][0]["locators"][1]["boxes"][0]["y0"] == 0.5 and body["items"][1]["locators"] == []

