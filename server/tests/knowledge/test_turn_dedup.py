"""检索结果按文档分组；同一轮里已经给过原文的片段再次命中只列编号（seen）。

只按「这一轮」记：前面轮次的原文可能已被压缩或省略，跨轮标 seen 会让模型找不到内容；子 Agent 的上下文
和根会话是分开的，根会话给过的它没见过，也不去重。"""

import json

import pytest

from knowwork_server.conversations.events_v1 import _search_summary
from knowwork_server.knowledge.models import Evidence, MetaItem, ReadResult
from knowwork_server.knowledge.service import SearchOutcome
from knowwork_server.knowledge.tools import SEEN_NOTE, knowledge_tools
from knowwork_server.knowledge.turn_ledger import TurnLedger
from knowwork_server.tools.registry import ToolContext

pytestmark = pytest.mark.anyio


def ev(doc: str, span: str, text: str, **kw) -> Evidence:
    return Evidence(source_id="builtin", kb_id="kb1", doc_ref=f"kb1/{doc}", span_ref=span, title=f"文档{doc}",
                    text=text, **kw)


class Fixed:
    """检索、阅读都回固定结果的知识服务替身。"""

    def __init__(self, *rounds: tuple[Evidence, ...]) -> None:
        self.rounds = list(rounds)

    async def caller(self, workspace_id, user_id):
        return None

    async def search(self, caller, query, **_):
        return SearchOutcome(evidence=self.rounds.pop(0))

    async def read(self, caller, source_id, doc_ref, **_):
        return ReadResult(doc_ref=doc_ref, title="文档", text="", spans=self.rounds.pop(0))


def tools(env, service, ledger):
    return {t.spec.name: t for t in knowledge_tools(service, env.knowledge.citations, ledger=ledger)}


def turn(ledger: TurnLedger, conversation: str, kind: str) -> None:
    ledger(conversation, "ws1", "session/event", {"type": kind, "data": {"turn": 1}})


async def search(tool, ctx, query="q"):
    return json.loads(await tool.handler(ctx, {"query": query}))


async def test_results_are_grouped_by_document_with_its_fields_once(env):
    """同一份文档的几条命中归到一起，文档的 source_id、doc_ref、标题、元数据只写一次；文档按最靠前的命中排。"""
    meta = (MetaItem(name="部门", value="财务部"),)
    found = (ev("a", "a1", "第一段", metadata=meta), ev("b", "b1", "别的文档"), ev("a", "a2", "第二段", metadata=meta))
    payload = await search(tools(env, Fixed(found), None)["knowledge_search"], ToolContext("ws1", "c-group", "alice"))
    assert payload["documents"] == [
        {"source_id": "builtin", "doc_ref": "kb1/a", "title": "文档a", "knowledge_base": "builtin.kb1",
         "metadata": {"部门": "财务部"}, "results": [{"ref": 1, "text": "第一段"}, {"ref": 3, "text": "第二段"}]},
        {"source_id": "builtin", "doc_ref": "kb1/b", "title": "文档b", "knowledge_base": "builtin.kb1",
         "results": [{"ref": 2, "text": "别的文档"}]},
    ]
    assert _search_summary(json.dumps(payload))["documents"] == ["builtin:kb1/a", "builtin:kb1/b"], "界面照样认得出"


async def test_a_snippet_already_given_this_turn_comes_back_as_its_ref_only(env):
    ledger = TurnLedger()
    first, again = (ev("a", "a1", "押金 500 元"), ev("a", "a2", "退宿时退还")), (ev("a", "a2", "退宿时退还"),
                                                                              ev("a", "a3", "扣除损坏赔偿"))
    tool = tools(env, Fixed(first, again), ledger)["knowledge_search"]
    ctx = ToolContext("ws1", "c-seen", "alice", agent_session_id="c-seen")
    turn(ledger, "c-seen", "turn/start")
    await search(tool, ctx)
    payload = await search(tool, ctx, "押金怎么退")
    assert payload["documents"][0]["results"] == [{"ref": 2, "seen": True}, {"ref": 3, "text": "扣除损坏赔偿"}]
    assert SEEN_NOTE in payload["notes"]


async def test_seen_is_only_for_this_turn(env):
    """下一轮从头记：上一轮的原文可能已经被压缩或省略，不能让模型去前面找。"""
    ledger = TurnLedger()
    same = (ev("a", "a1", "押金 500 元"),)
    tool = tools(env, Fixed(same, same, same), ledger)["knowledge_search"]
    ctx = ToolContext("ws1", "c-turns", "alice")
    turn(ledger, "c-turns", "turn/start")
    await search(tool, ctx)
    turn(ledger, "c-turns", "turn/end")
    turn(ledger, "c-turns", "turn/start")
    assert (await search(tool, ctx))["documents"][0]["results"] == [{"ref": 1, "text": "押金 500 元"}]
    unseen_turn = ToolContext("ws1", "c-unknown", "alice")
    await search(tool, unseen_turn)
    assert ledger.shown("c-unknown") == frozenset(), "没看到这一轮开始的会话不记，宁可不去重"


async def test_subagents_get_the_full_text(env):
    """子 Agent 用根会话的令牌，但上下文是自己的：根会话给过的原文它没见过。"""
    ledger = TurnLedger()
    same = (ev("a", "a1", "押金 500 元"),)
    tool = tools(env, Fixed(same, same), ledger)["knowledge_search"]
    turn(ledger, "c-sub", "turn/start")
    await search(tool, ToolContext("ws1", "c-sub", "alice", agent_session_id="c-sub"))
    child = await search(tool, ToolContext("ws1", "c-sub", "alice", agent_session_id="c-sub:child-1"))
    assert child["documents"][0]["results"] == [{"ref": 1, "text": "押金 500 元"}]


async def test_what_was_read_counts_as_given(env):
    """knowledge_read 读到的段落这一轮模型已经有了，之后检索命中只列编号；阅读本身不去重（是模型要读的）。"""
    ledger = TurnLedger()
    passage = (ev("a", "a1", "押金 500 元"),)
    named = tools(env, Fixed(passage, passage, passage), ledger)
    ctx = ToolContext("ws1", "c-read", "alice")
    turn(ledger, "c-read", "turn/start")
    await named["knowledge_read"].handler(ctx, {"source_id": "builtin", "doc_ref": "kb1/a"})
    again = json.loads(await named["knowledge_read"].handler(ctx, {"source_id": "builtin", "doc_ref": "kb1/a"}))
    assert again["passages"][0]["text"] == "押金 500 元"
    assert (await search(named["knowledge_search"], ctx))["documents"][0]["results"] == [{"ref": 1, "seen": True}]


async def test_parallel_searches_in_one_step_each_get_the_full_text(env):
    """同一步里并行的几次检索（工具声明了 concurrent）：结果一起进上下文，可以一条给原文、一条标 seen，
    但不能两条都只有编号；编号一致。"""
    import asyncio

    ledger = TurnLedger()
    same = (ev("a", "a1", "押金 500 元"),)
    tool = tools(env, Fixed(same, same), ledger)["knowledge_search"]
    ctx = ToolContext("ws1", "c-par", "alice")
    turn(ledger, "c-par", "turn/start")
    one, two = await asyncio.gather(search(tool, ctx, "押金"), search(tool, ctx, "押金多少"))
    pieces = [one["documents"][0]["results"][0], two["documents"][0]["results"][0]]
    assert {"ref": 1, "text": "押金 500 元"} in pieces and all(p["ref"] == 1 for p in pieces)


def test_only_read_only_tools_may_run_side_by_side():
    from knowwork_server.tools.registry import KW_NOW, ToolSpec

    specs = {t.spec.name: t.spec.describe() for t in knowledge_tools(Fixed(), None)}
    assert all(spec["concurrent"] is True for spec in specs.values())
    assert KW_NOW.spec.describe()["concurrent"] is True
    assert "concurrent" not in ToolSpec("kw_w", "写入", {"type": "object"}, "write").describe()
    with pytest.raises(ValueError, match="不是只读"):
        ToolSpec("kw_w", "写入", {"type": "object"}, "write", concurrent=True)


def test_read_only_knowledge_tools_are_offered_in_quick_answers():
    """快速问答里检索、阅读、看库、列文档都给；没写回答方式的工具只在智能体里，描述里不带这个字段。"""
    from knowwork_server.tools.registry import ToolSpec

    specs = {t.spec.name: t.spec.describe() for t in knowledge_tools(Fixed(), None)}
    assert {name: spec["answerModes"] for name, spec in specs.items()} == {
        name: ["agent", "quick"] for name in ("knowledge_search", "knowledge_read", "knowledge_bases", "knowledge_documents")}
    assert "answerModes" not in ToolSpec("kw_w", "写入", {"type": "object"}, "write").describe()
    for bad in (frozenset(), frozenset({"fast"})):
        with pytest.raises(ValueError, match="回答方式"):
            ToolSpec("kw_w", "写入", {"type": "object"}, "write", answer_modes=bad)
