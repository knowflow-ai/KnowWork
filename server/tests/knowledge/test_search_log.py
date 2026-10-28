"""知识检索记录：检索时记（轮次号从每轮汇总取），回合结束时补轮次号、标出被引用的命中，交回检索次数与引用条数；
删会话时一起删；记录失败不影响检索。"""

import json

import pytest
from sqlalchemy import select

from knowwork_server.access.rls import set_workspace
from knowwork_server.conversations.summaries import TurnEnded
from knowwork_server.jobs import Job
from knowwork_server.knowledge.answer_check import KIND
from knowwork_server.knowledge.models import Evidence, SearchTraceEntry
from knowwork_server.knowledge.search_log import SearchLog
from knowwork_server.knowledge.service import SearchOutcome
from knowwork_server.knowledge.tables import KnowledgeAnswerCheck, KnowledgeSearchHit, KnowledgeSearchLog
from knowwork_server.knowledge.tools import knowledge_tools
from knowwork_server.tools.registry import ToolContext

pytestmark = pytest.mark.anyio


def ev(doc: str, span: str) -> Evidence:
    return Evidence(source_id="builtin", kb_id="kb1", doc_ref=f"kb1/{doc}", span_ref=span, title=f"文档{doc}",
                    text=f"{span} 的正文")


class Fixed:
    """每次检索回下一组固定结果的知识服务替身。"""

    def __init__(self, *rounds: tuple[Evidence, ...]) -> None:
        self.rounds = list(rounds)

    async def caller(self, workspace_id, user_id):
        return None

    async def search(self, caller, query, **_):
        return SearchOutcome(evidence=self.rounds.pop(0))


class Turns:
    """每轮汇总那边告诉检索记录的「正在跑第几轮」。"""

    def __init__(self) -> None:
        self.now: dict[str, int] = {}

    def __call__(self, conversation_id: str) -> int | None:
        return self.now.get(conversation_id)


def search_tool(env, service, log: SearchLog, name: str = "knowledge_search"):
    return {t.spec.name: t for t in knowledge_tools(service, env.knowledge.citations, search_log=log)}[name]


async def run(tool, conversation: str, query: str) -> dict:
    return json.loads(await tool.handler(ToolContext("ws1", conversation, "alice"), {"query": query}))


def ended(conversation: str, turn: int, answer: str) -> TurnEnded:
    return TurnEnded(workspace_id="ws1", conversation_id=conversation, turn=turn, question="", answer=answer,
                     status="completed")


async def rows(env, model, conversation: str):
    async with env.sessionmaker() as session, session.begin():
        await set_workspace(session, "ws1")
        if model is KnowledgeSearchHit:
            stmt = (select(KnowledgeSearchHit).join(KnowledgeSearchLog)
                    .where(KnowledgeSearchLog.conversation_id == conversation)
                    .order_by(KnowledgeSearchLog.created_at, KnowledgeSearchHit.rank))
        else:
            stmt = select(model).where(model.conversation_id == conversation).order_by(model.created_at)
        return (await session.scalars(stmt)).all()


async def test_a_turn_records_its_searches_and_which_hits_the_answer_cited(env):
    turns = Turns()
    log = SearchLog(env.sessionmaker, turns)
    tool = search_tool(env, Fixed((ev("a", "a1"), ev("b", "b1")), (ev("a", "a1"), ev("c", "c1"))), log)
    turns.now["c-1"] = 3
    await run(tool, "c-1", "报销流程")
    await run(tool, "c-1", "差旅报销")
    facts = await log.close_turn(ended("c-1", 3, "先填单 [1]，再审批。\n[IMG:3:0]"))  # 放了第 3 条的图也算引用了它
    assert facts == {"searches": 2, "cited": 2, "bases": ["builtin.kb1"], "cited_bases": ["builtin.kb1"]}
    logs = await rows(env, KnowledgeSearchLog, "c-1")
    assert [(entry.query, entry.turn, entry.user_id) for entry in logs] == [("报销流程", 3, "alice"),
                                                                           ("差旅报销", 3, "alice")]
    assert logs[0].options == {"mode": "hybrid", "bases": [], "conditions": []}
    hits = await rows(env, KnowledgeSearchHit, "c-1")
    assert [(hit.number, hit.doc_ref, hit.kb_id, hit.cited) for hit in hits] == [
        (1, "kb1/a", "kb1", True), (2, "kb1/b", "kb1", False), (1, "kb1/a", "kb1", True), (3, "kb1/c", "kb1", True)]


async def test_a_turn_without_searches_gives_no_facts(env):
    assert await SearchLog(env.sessionmaker).close_turn(ended("c-2", 1, "你好")) is None


async def test_citing_an_earlier_turn_counts_and_hits_are_marked_only_in_their_own_turn(env):
    """引用了几条按会话的引用目录算：这一轮引用前一轮查到的片段也算引用了；什么都没引用的 cited 为 0。"""
    turns = Turns()
    log = SearchLog(env.sessionmaker, turns)
    tool = search_tool(env, Fixed((ev("a", "a1"),), (ev("b", "b1"),), (ev("c", "c1"),)), log)
    facts = []
    for turn, (question, reply) in enumerate((("押金多少", "500 元 [1]"), ("怎么退", "退宿时退还 [1]"),
                                              ("能分期吗", "资料里没有写")), start=1):
        turns.now["c-3"] = turn
        await run(tool, "c-3", question)
        facts.append(await log.close_turn(ended("c-3", turn, reply)))
    assert [(f["searches"], f["cited"], f["cited_bases"]) for f in facts] == [
        (1, 1, ["builtin.kb1"]), (1, 1, []), (1, 0, [])], "第二轮引用的是第一轮查到的，这一轮查到的库没用上"
    assert [hit.cited for hit in await rows(env, KnowledgeSearchHit, "c-3")] == [True, False, False]


async def test_searches_whose_turn_was_unknown_are_claimed_when_it_ends(env):
    """进程刚重启、多副本时检索落到别的副本：检索时不知道第几轮，回合结束时归到这一轮。"""
    log = SearchLog(env.sessionmaker)
    await run(search_tool(env, Fixed((ev("a", "a1"),)), log), "c-4", "合同期限")
    assert [entry.turn for entry in await rows(env, KnowledgeSearchLog, "c-4")] == [None]
    assert await log.close_turn(ended("c-4", 7, "")) == {"searches": 1, "cited": 0, "bases": ["builtin.kb1"],
                                                       "cited_bases": []}
    assert [entry.turn for entry in await rows(env, KnowledgeSearchLog, "c-4")] == [7]


async def test_deleting_the_conversation_erases_its_searches(env):
    log = SearchLog(env.sessionmaker)
    await run(search_tool(env, Fixed((ev("a", "a1"),)), log), "c-5", "q")
    await log.discard("ws1", "c-5")
    for model in (KnowledgeSearchLog, KnowledgeSearchHit):
        assert await rows(env, model, "c-5") == []


async def test_a_failing_record_does_not_break_the_search(env):
    def broken():
        raise RuntimeError("数据库连不上")

    payload = await run(search_tool(env, Fixed((ev("a", "a1"),)), SearchLog(broken)), "c-6", "q")
    assert payload["documents"][0]["results"] == [{"ref": 1, "text": "a1 的正文"}]


async def all_of(env, model):
    async with env.sessionmaker() as session, session.begin():
        await set_workspace(session, "ws1")
        return (await session.scalars(select(model))).all()


async def test_a_completed_turn_that_searched_waits_to_be_judged(env):
    """检索过、正常答完的一轮记一条待判断（问题和最终回答），这个空间排一条一小时后的任务；后面的回合并进同一条，
    所以一小时一批。答上没有晚些由 answer_check 补进事实，这里不给。没检索过、没正常答完的不判；删会话时一起删。"""
    turns = Turns()
    log = SearchLog(env.sessionmaker, turns)
    tool = search_tool(env, Fixed((ev("a", "a1"),), (ev("b", "b1"),), (ev("c", "c1"),)), log)
    for conversation in ("c-7", "c-8", "c-9"):
        turns.now[conversation] = 1
        await run(tool, conversation, "会员续费退款规则")
    facts = await log.close_turn(TurnEnded(workspace_id="ws1", conversation_id="c-7", turn=1, question="退款规则",
                                           answer="资料里没有退款规则，只有付费方式 [1]", status="completed"))
    assert (facts["searches"], facts["cited"]) == (1, 1) and "verdict" not in facts
    await log.close_turn(ended("c-8", 1, "年假 5 天 [1]"))
    await log.close_turn(TurnEnded(workspace_id="ws1", conversation_id="c-9", turn=1, question="", answer="",
                                   status="cancelled"))
    assert await log.close_turn(ended("c-10", 1, "你好")) is None  # 没检索过

    checks = sorted(await all_of(env, KnowledgeAnswerCheck), key=lambda c: c.conversation_id)
    assert [(c.conversation_id, c.turn) for c in checks] == [("c-7", 1), ("c-8", 1)]
    assert (checks[0].question, checks[0].answer) == ("退款规则", "资料里没有退款规则，只有付费方式 [1]")
    jobs = [job for job in await all_of(env, Job) if job.kind == KIND.name]
    assert [(job.subject_id, job.status) for job in jobs] == [("ws1", "queued")]
    assert (jobs[0].next_attempt_at - jobs[0].created_at).total_seconds() > 3500

    await log.discard("ws1", "c-7")
    assert [c.conversation_id for c in await all_of(env, KnowledgeAnswerCheck)] == ["c-8"]

class Traced:
    """检索时交回检索过程的知识服务替身。"""

    async def caller(self, workspace_id, user_id):
        return None

    async def search(self, caller, query, **_):
        return SearchOutcome(evidence=(ev("a", "a1"),),
                             trace=(SearchTraceEntry(label="视觉检索", summary="命中 1 页", source="扩展知识库"),))


async def test_the_search_trace_is_kept_by_call_id_for_run_details_but_not_shown_to_the_model(env):
    log = SearchLog(env.sessionmaker)
    tool = search_tool(env, Traced(), log)
    payload = json.loads(await tool.handler(ToolContext("ws1", "c-9", "alice", call_id="call-1"), {"query": "流程"}))
    assert "视觉检索" not in json.dumps(payload, ensure_ascii=False), "检索过程不给模型"
    found = await log.traces("ws1", "c-9", ["call-1", "call-2"])
    assert found == {"call-1": [{"label": "视觉检索", "summary": "命中 1 页", "lines": [], "source": "扩展知识库"}]}
    assert await log.traces("ws1", "c-other", ["call-1"]) == {}, "只认这个会话的"
