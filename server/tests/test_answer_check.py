"""回合结束后判断知识库答上了没有（knowledge.answer_check）：取这个空间等着的回合，每次调用判 5 轮，给模型看问题、
最终回答、这一轮检索到的片段原文，以及前几轮问了什么、用户紧接着说了什么；分级结论、原因、相关片段出自哪个库、用户的
反应（纠正、换说法）并进每轮汇总的知识事实，判完删掉待判断的那一行。还在等下一条消息的先不判；返回看不懂的留着下次
再判，模型调不通按服务问题重试。

用户给这一轮点了踩的，判断时也给模型看（选的分类、写的说明），点了踩就不再等下一条消息；结论里记下点了踩和说明。
已经判过的轮次后来被点踩或撤回了踩，检索过知识库、在 RERATE_DAYS 天内的，从会话记录里取回最后的回答重新排进待判断。"""

import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from knowwork_server.conversations.feedback import FeedbackStore
from knowwork_server.conversations.models import AnswerFeedback, Conversation, RunEvent, RunSummary
from knowwork_server.jobs import Done, Job, JobError
from knowwork_server.knowledge.answer_check import (
    CITED_CHARS,
    KIND,
    MAX_CHECK_TRIES,
    PER_CALL,
    RERATE_DAYS,
    SNIPPET_CHARS,
    SNIPPETS_BUDGET,
    AnswerChecker,
    parse_verdicts,
    requeue_rated,
)
from knowwork_server.knowledge.tables import (
    Citation,
    CitationCatalogRow,
    KnowledgeAnswerCheck,
    KnowledgeSearchHit,
    KnowledgeSearchLog,
)
from knowwork_server.models.client import ModelCallError

pytestmark = pytest.mark.anyio


class Stage:
    def __init__(self) -> None:
        self.detail: dict = {}

    def record(self, **values) -> None:
        self.detail = {**self.detail, **values}


class Ctx:
    """任务上下文替身：只要空间和阶段。共用的测试库不清空，每个用例用自己的空间。"""

    def __init__(self) -> None:
        self.workspace_id = f"ws-{uuid.uuid4().hex[:8]}"

    @asynccontextmanager
    async def stage(self, name: str, **detail):
        yield Stage()


class Models:
    """模型替身：按调用顺序给回复（函数的话拿这次看到的待判断项算回复），记下每次的输入。"""

    def __init__(self, *replies) -> None:
        self.replies = list(replies)
        self.calls: list[list[dict]] = []

    async def chat(self, workspace_id, alias, messages, **_):
        items = json.loads(messages[-1]["content"])
        self.calls.append(items)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply(items) if callable(reply) else reply


def checker(sessionmaker, models, *, wait_seconds: float = 0.0) -> AnswerChecker:
    """测试里的回合都是刚答完的：默认不等下一条消息，要测「等」的用例自己给 wait_seconds"""
    return AnswerChecker(sessionmaker, models, wait_seconds=wait_seconds)


def results(*verdicts):
    return lambda items: json.dumps({"results": [
        {"id": item["id"], **verdict} for item, verdict in zip(items, verdicts, strict=False)]}, ensure_ascii=False)


async def seed(sessionmaker, turns: int) -> Ctx:
    """一个新空间里会话的 1..turns 轮：都检索过（命中引用目录里的第 1、2 段）、正常答完，等着判断。"""
    ctx = Ctx()
    ws, c1 = ctx.workspace_id, f"{ctx.workspace_id}-c1"
    async with sessionmaker() as session, session.begin():
        session.add(Conversation(id=c1, workspace_id=ws, user_id="alice", title="制度"))
        session.add(CitationCatalogRow(id=c1, workspace_id=ws, limit=100))
        await session.flush()
        session.add_all([
            Citation(catalog_id=c1, workspace_id=ws, number=1, source_id="builtin", doc_ref="kb1/a", span_ref="a1",
                     title="费用说明", text="付费方式：按月 \n 按年"),
            Citation(catalog_id=c1, workspace_id=ws, number=2, source_id="builtin", doc_ref="kb1/b", span_ref="b1",
                     title="请假制度", text="年假 5 天"),
            # 前几轮查到的、这一轮检索名次里没有，回答却引用了
            Citation(catalog_id=c1, workspace_id=ws, number=13, source_id="builtin", doc_ref="kb1/c", span_ref="c1",
                     title="报销规定", text="报销须在发生后 30 天内提交")])
        for turn in range(1, turns + 1):
            session.add(RunSummary(conversation_id=c1, turn=turn, workspace_id=ws, user_id="alice",
                                   question=f"第 {turn} 个问题",
                                   status="completed", started_at=datetime.now(UTC),
                                   facts={"knowledge": {"searches": 1, "cited": 1}}))
            session.add(KnowledgeSearchLog(id=f"{ws}-log{turn}", workspace_id=ws, conversation_id=c1, turn=turn,
                                           user_id="alice", query="退款", options={}))
            await session.flush()
            session.add_all([KnowledgeSearchHit(log_id=f"{ws}-log{turn}", rank=rank, workspace_id=ws, number=number,
                                                source_id="builtin", kb_id="kb1", doc_ref="d", span_ref="s", title="t",
                                                score=0.5, cited=False) for rank, number in ((0, 2), (1, 1))])
            session.add(KnowledgeAnswerCheck(id=f"{ws}-kac{turn}", workspace_id=ws, conversation_id=c1, turn=turn,
                                             question=f"第 {turn} 个问题", answer=f"第 {turn} 个回答 [13]"))
    return ctx


async def facts_of(sessionmaker, ctx: Ctx) -> dict[int, dict]:
    async with sessionmaker() as session:
        rows = (await session.scalars(select(RunSummary).where(RunSummary.workspace_id == ctx.workspace_id)
                                      .order_by(RunSummary.turn))).all()
        return {row.turn: row.facts["knowledge"] for row in rows}


async def pending(sessionmaker, ctx: Ctx) -> list[int]:
    async with sessionmaker() as session:
        return sorted((await session.scalars(select(KnowledgeAnswerCheck.turn).where(
            KnowledgeAnswerCheck.workspace_id == ctx.workspace_id))).all())


async def test_judges_in_batches_and_merges_the_verdict_into_the_turn_facts(sessionmaker):
    ctx = await seed(sessionmaker, PER_CALL + 1)
    gap = {"verdict": "partial", "missing": "会员续费的退款规则", "reason": "content_missing"}
    models = Models(results(*[{"verdict": "answered"}] * (PER_CALL - 1), gap), results({"verdict": "unanswered"}))
    assert await checker(sessionmaker, models).handle(ctx) is None
    assert [len(call) for call in models.calls] == [PER_CALL, 1]
    first = models.calls[0][0]
    assert (first["question"], first["answer"]) == ("第 1 个问题", "第 1 个回答 [13]")
    assert first["snippets"] == [
        {"ref": 13, "title": "报销规定", "text": "报销须在发生后 30 天内提交"},
        {"ref": 2, "title": "请假制度", "text": "年假 5 天"},
        {"ref": 1, "title": "费用说明", "text": "付费方式：按月 按年"},
    ], "先给回答标了编号的原文（名次里没有也给），再按这一轮检索的名次"

    facts = await facts_of(sessionmaker, ctx)
    assert facts[1] == {"searches": 1, "cited": 1, "verdict": "answered"}
    assert facts[PER_CALL] == {"searches": 1, "cited": 1, "verdict": "partial", "missing": "会员续费的退款规则",
                               "reason": "content_missing"}
    assert facts[PER_CALL + 1]["verdict"] == "unanswered" and facts[PER_CALL + 1]["reason"] == "content_missing", \
        "没写原因的按「片段里没有」记，交给核实"
    assert await pending(sessionmaker, ctx) == []


async def seed_long(sessionmaker, answer: str, texts: dict[int, str], *, images: dict[int, list] | None = None,
                    ranked: tuple[int, ...] = (), tools: tuple[str, ...] = ()) -> Ctx:
    """一轮等着判断：引用目录里放 texts 这几段（可带图），这一轮检索的名次是 ranked。"""
    ctx = Ctx()
    ws, c1 = ctx.workspace_id, f"{ctx.workspace_id}-c1"
    async with sessionmaker() as session, session.begin():
        session.add(Conversation(id=c1, workspace_id=ws, user_id="alice", title="微短剧"))
        session.add(CitationCatalogRow(id=c1, workspace_id=ws, limit=100))
        await session.flush()
        session.add_all([Citation(catalog_id=c1, workspace_id=ws, number=number, source_id="builtin", doc_ref="kb1/r",
                                  span_ref=f"r{number}", title="行业报告", text=text,
                                  images=(images or {}).get(number)) for number, text in texts.items()])
        session.add(RunSummary(conversation_id=c1, turn=1, workspace_id=ws, user_id="alice", question="代表作品有哪些",
                               status="completed", started_at=datetime.now(UTC), tools=list(tools),
                               facts={"knowledge": {"searches": 1}}))
        session.add(KnowledgeSearchLog(id=f"{ws}-log1", workspace_id=ws, conversation_id=c1, turn=1, user_id="alice",
                                       query="代表作品", options={}))
        await session.flush()
        session.add_all([KnowledgeSearchHit(log_id=f"{ws}-log1", rank=rank, workspace_id=ws, number=number,
                                            source_id="builtin", kb_id="kb1", doc_ref="d", span_ref="s", title="t",
                                            score=0.5, cited=False) for rank, number in enumerate(ranked)])
        session.add(KnowledgeAnswerCheck(id=f"{ws}-kac1", workspace_id=ws, conversation_id=c1, turn=1,
                                         question="代表作品有哪些", answer=answer))
    return ctx


async def item_seen(sessionmaker, ctx: Ctx) -> dict:
    models = Models(results({"verdict": "answered"}))
    await checker(sessionmaker, models).handle(ctx)
    return models.calls[0][0]


async def snippets_seen(sessionmaker, ctx: Ctx) -> list[dict]:
    return (await item_seen(sessionmaker, ctx))["snippets"]


async def test_a_cited_snippet_is_given_in_full_so_what_the_answer_took_from_its_second_half_can_be_checked(
        sessionmaker):
    # 2026-10-29：片段 563 字，《招惹》的主演、播放量在第 400 字之后；原来一律截 400 字，答对了被判成只答了一部分
    cited = "《东栏雪》" + "介" * 450 + "《招惹》主演：李沐宸；播放量：2.0亿+"
    ranked_only = "背景" * 400
    ctx = await seed_long(sessionmaker, "《招惹》播放量 2.0 亿+ [33]", {33: cited, 7: ranked_only}, ranked=(7, 33))
    snippets = await snippets_seen(sessionmaker, ctx)
    assert [s["ref"] for s in snippets] == [33, 7]
    assert snippets[0]["text"] == cited, "回答标了编号的片段给全文"
    assert len(snippets[1]["text"]) == SNIPPET_CHARS and snippets[1]["text"].endswith("…"), "按名次补的截短并标出截了"


async def test_cited_snippets_stop_at_their_cap_and_the_turn_stays_within_the_budget(sessionmaker):
    long = "条" * (CITED_CHARS * 2)
    texts = {number: long for number in range(1, 11)}
    ctx = await seed_long(sessionmaker, "".join(f"[{n}]" for n in texts), texts)
    snippets = await snippets_seen(sessionmaker, ctx)
    assert all(len(s["text"]) <= CITED_CHARS for s in snippets)
    assert snippets[0]["text"].endswith("…")
    assert sum(len(s["text"]) for s in snippets) <= SNIPPETS_BUDGET


async def test_a_cited_snippet_brings_its_image_descriptions_in_order(sessionmaker):
    images = {5: [{"ref": "i0", "caption": "《东栏雪》海报：古装女子 \n 立于雪中"}, {"ref": "i1"}]}
    ctx = await seed_long(sessionmaker, "海报如下 [IMG:5:0]", {5: "宣传图", 6: "别的"}, images={**images, 6: images[5]},
                          ranked=(6,))
    snippets = await snippets_seen(sessionmaker, ctx)
    assert snippets[0] == {"ref": 5, "title": "行业报告", "text": "宣传图",
                           "images": ["《东栏雪》海报：古装女子 立于雪中", "无描述"]}
    assert "images" not in snippets[1], "没被引用的不带图的描述"


@pytest.mark.parametrize(("tools", "looked"), [
    (("knowledge_search", "knowledge_fetch", "read_image"), True),
    (("knowledge_search", "read_image"), False),  # 只看过图：可能是用户传的附件
    (("knowledge_search", "knowledge_fetch"), False),
])
async def test_the_judge_is_told_when_the_agent_looked_at_the_knowledge_base_images(sessionmaker, tools, looked):
    ctx = await seed_long(sessionmaker, "海报左侧是主海报 [IMG:5:0]", {5: "宣传图"}, tools=tools)
    assert (await item_seen(sessionmaker, ctx)).get("looked_at_images", False) is looked


async def test_turns_without_a_readable_verdict_wait_for_the_next_batch(sessionmaker):
    ctx = await seed(sessionmaker, 2)
    models = Models(lambda items: "好的：" + json.dumps({"results": [
        {"id": items[0]["id"], "verdict": "answered"}, {"id": items[1]["id"], "verdict": "maybe"}]}))
    await checker(sessionmaker, models).handle(ctx)
    assert await pending(sessionmaker, ctx) == [2]
    assert "verdict" not in (await facts_of(sessionmaker, ctx))[2]


async def test_a_turn_that_never_gets_a_verdict_is_given_up_so_it_cannot_block_newer_ones(sessionmaker):
    """判几次都拿不到结论就放弃：不然它一直排在最前面，攒多了把后面新的回合挡住，还每次白调模型。不算没答上。"""
    ctx = await seed(sessionmaker, 1)
    models = Models(*["看不懂"] * MAX_CHECK_TRIES)
    for tried in range(1, MAX_CHECK_TRIES + 1):
        await checker(sessionmaker, models).handle(ctx)
        assert await pending(sessionmaker, ctx) == ([] if tried == MAX_CHECK_TRIES else [1])
    assert len(models.calls) == MAX_CHECK_TRIES
    assert "verdict" not in (await facts_of(sessionmaker, ctx))[1]


async def test_a_regenerated_answer_judged_again_drops_the_old_gap(sessionmaker):
    """同一轮重新生成回答再判一次：上次判的缺什么、原因去掉，这次答上了就只留答上了。"""
    ctx = await seed(sessionmaker, 1)
    async with sessionmaker() as session, session.begin():
        summary = await session.get(RunSummary, (f"{ctx.workspace_id}-c1", 1))
        summary.facts = {"knowledge": {"searches": 1, "cited": 1, "verdict": "partial", "missing": "旧的",
                                       "reason": "content_missing", "follow": "corrected", "correction": "旧的"}}
    await checker(sessionmaker, Models(results({"verdict": "answered"}))).handle(ctx)
    assert (await facts_of(sessionmaker, ctx))[1] == {"searches": 1, "cited": 1, "verdict": "answered"}


async def test_a_model_that_cannot_be_reached_is_retried_as_a_service_problem(sessionmaker):
    ctx = await seed(sessionmaker, 1)
    models = Models(ModelCallError("model_unavailable", "连不上模型服务", retryable=True))
    with pytest.raises(JobError) as raised:
        await checker(sessionmaker, models).handle(ctx)
    assert (raised.value.code, raised.value.error_class, raised.value.retryable) == (
        "model_unavailable", "service", True)
    assert await pending(sessionmaker, ctx) == [1]


def test_parse_verdicts_skips_what_it_cannot_use():
    text = '```json\n{"results": [{"id": "a", "verdict": "unanswered", "missing": " 年假\\n天数 ", "reason": "x",' \
           ' "refs": [2, "3", null], "follow": "maybe"}, {"id": "zzz", "verdict": "answered"}, {"id": "b"}]}\n```'
    verdicts = parse_verdicts(text, ["a", "b"])
    assert list(verdicts) == ["a"]
    assert verdicts["a"].facts({2: "builtin.kb1"}) == {
        "verdict": "unanswered", "missing": "年假 天数", "reason": "content_missing", "about": ["builtin.kb1"]}, \
        "认不出的原因按「片段里没有」，认不出的反应不记，相关片段只认数字"
    assert parse_verdicts("看不懂", ["a"]) == {}


async def test_the_judge_sees_what_was_asked_before_and_what_the_user_said_next(sessionmaker):
    """追问（「他是哪一年成立」）只看这一句判不了；用户下一句说「不对」、换了个说法重问，都说明这一轮没答好"""
    ctx = await seed(sessionmaker, 3)
    models = Models(results({"verdict": "answered"}, {"verdict": "answered"}, {"verdict": "answered"}))
    await checker(sessionmaker, models).handle(ctx)
    items = {item["question"]: item for item in models.calls[0]}
    assert "context" not in items["第 1 个问题"] and items["第 1 个问题"]["next_message"] == "第 2 个问题"
    assert items["第 2 个问题"]["context"] == ["第 1 个问题"] and items["第 2 个问题"]["next_message"] == "第 3 个问题"
    assert items["第 3 个问题"]["context"] == ["第 1 个问题", "第 2 个问题"] and "next_message" not in items["第 3 个问题"]


async def test_a_turn_still_waiting_for_the_next_message_is_judged_later(sessionmaker):
    """刚答完、用户还没接着说的先不判（纠正、换说法都在下一条里）；有下一条的照判，等着的稍后再排一次"""
    ctx = await seed(sessionmaker, 2)
    models = Models(results({"verdict": "answered"}))
    done = await checker(sessionmaker, models, wait_seconds=600).handle(ctx)
    assert [item["question"] for item in models.calls[0]] == ["第 1 个问题"]
    assert await pending(sessionmaker, ctx) == [2]
    assert isinstance(done, Done) and done.apply is not None, "等着的那几轮稍后再排一次，不靠下一轮来触发"


async def test_reasons_reactions_and_the_knowledge_base_it_was_about_are_recorded(sessionmaker):
    """原因分细（不该知识库答、片段里没有、含糊或过时、没用上、模型自己的知识）；相关片段对到它出自的库；用户纠正
    摘出原话、换说法记下新说法：这些是后面配置建议（同义词、版本过期……）的信号"""
    ctx = await seed(sessionmaker, 3)
    models = Models(results(
        {"verdict": "partial", "reason": "content_unclear", "missing": "最新的住宿标准", "refs": [2, 13],
         "follow": "corrected", "correction": "住宿标准现在是 500"},
        {"verdict": "unanswered", "reason": "content_missing", "missing": "差旅补贴", "refs": [],
         "follow": "rephrased", "rephrased": "出差补助怎么算"},
        {"verdict": "unanswered", "reason": "not_for_kb", "follow": "satisfied"}))
    await checker(sessionmaker, models).handle(ctx)
    facts = await facts_of(sessionmaker, ctx)
    assert facts[1] == {"searches": 1, "cited": 1, "verdict": "partial", "reason": "content_unclear",
                        "missing": "最新的住宿标准", "about": ["builtin.kb1"], "follow": "corrected",
                        "correction": "住宿标准现在是 500"}, "第 13 段这一轮没检索到，查不到出自哪个库就不算"
    assert facts[2]["follow"] == "rephrased" and facts[2]["rephrased"] == "出差补助怎么算" and "about" not in facts[2]
    assert facts[3]["reason"] == "not_for_kb" and "follow" not in facts[3], "满意了、看不出来的不记反应"


async def rate(sessionmaker, ctx: Ctx, turn: int, rating: str, *, category: str | None = None,
               note: str | None = None) -> None:
    async with sessionmaker() as session, session.begin():
        session.add(AnswerFeedback(conversation_id=f"{ctx.workspace_id}-c1", run_id=f"run-{turn}",
                                   workspace_id=ctx.workspace_id, user_id="alice", rating=rating, category=category,
                                   note=note))


async def test_a_dislike_is_shown_to_the_judge_and_the_turn_does_not_wait_for_the_next_message(sessionmaker):
    """点了踩就是用户的反应：判断看得到分类和说明（点赞不给），不再等下一条消息；结论里记下点了踩和说明，配置建议
    拿它当证据（写了说明的分量和纠正一样）"""
    ctx = await seed(sessionmaker, 3)
    await rate(sessionmaker, ctx, 1, "like")
    await rate(sessionmaker, ctx, 3, "dislike", category="task-result", note="住宿标准写错了，现在是 600")
    models = Models(results({"verdict": "answered"}, {"verdict": "answered"},
                            {"verdict": "unanswered", "reason": "content_unclear", "missing": "住宿标准"}))
    await checker(sessionmaker, models, wait_seconds=600).handle(ctx)
    items = {item["question"]: item for item in models.calls[0]}
    assert set(items) == {"第 1 个问题", "第 2 个问题", "第 3 个问题"}, "第 3 轮没有下一条，但点了踩，不等"
    assert items["第 3 个问题"]["feedback"] == {"category": "任务结果", "note": "住宿标准写错了，现在是 600"}
    assert "feedback" not in items["第 1 个问题"], "点赞不给判断看"
    facts = await facts_of(sessionmaker, ctx)
    assert (facts[3]["disliked"], facts[3]["dislike_note"]) == (True, "住宿标准写错了，现在是 600")
    assert "disliked" not in facts[1]


async def seed_events(sessionmaker, ctx: Ctx, turn: int, answer: str) -> None:
    """会话记录里这一轮的事件：开始、两条助手消息（后一条是最终回答）、结束。"""
    frames = [{"type": "turn/start", "data": {"turn": turn}},
              {"type": "assistant/message", "data": {"message": {"content": "我先查一下"}}},
              {"type": "assistant/message", "data": {"message": {"content": [{"type": "text", "text": answer}]}}},
              {"type": "turn/end", "data": {"turn": turn}}]
    async with sessionmaker() as session, session.begin():
        for seq, data in enumerate(frames, start=turn * 10):
            session.add(RunEvent(conversation_id=f"{ctx.workspace_id}-c1", seq=seq, workspace_id=ctx.workspace_id,
                                 host_epoch="e1", host_cursor=seq, kind="session/event", data=data, ui=[]))


async def test_a_late_dislike_puts_a_judged_turn_back_with_its_answer_from_the_conversation_record(sessionmaker):
    """判完以后才点的踩（或撤回了踩）：重新排进待判断，回答从会话记录里取回最后一条助手回答；这一轮的判断任务也排上"""
    ctx = await seed(sessionmaker, 2)
    await checker(sessionmaker, Models(results({"verdict": "answered"}, {"verdict": "answered"}))).handle(ctx)
    assert await pending(sessionmaker, ctx) == []
    await seed_events(sessionmaker, ctx, 1, "第 1 个回答（最终）[13]")
    await seed_events(sessionmaker, ctx, 2, "第 2 个回答")
    assert await requeue_rated(sessionmaker, ctx.workspace_id, f"{ctx.workspace_id}-c1", 1) is True
    assert await pending(sessionmaker, ctx) == [1]
    async with sessionmaker() as session:
        check = await session.scalar(select(KnowledgeAnswerCheck).where(
            KnowledgeAnswerCheck.workspace_id == ctx.workspace_id))
        assert (check.question, check.answer) == ("第 1 个问题", "第 1 个回答（最终）[13]")
        assert await session.scalar(select(Job.id).where(Job.workspace_id == ctx.workspace_id, Job.kind == KIND.name))
    assert await requeue_rated(sessionmaker, ctx.workspace_id, f"{ctx.workspace_id}-c1", 1) is False, \
        "已经在等着判断的不重复排（判的时候会看到最新的评分）"


async def test_a_late_dislike_is_not_queued_without_a_search_or_after_the_window(sessionmaker):
    """没检索知识库的轮次本来就不判；太久以前的（超过 RERATE_DAYS 天）不再翻出来"""
    ctx = await seed(sessionmaker, 2)
    await checker(sessionmaker, Models(results({"verdict": "answered"}, {"verdict": "answered"}))).handle(ctx)
    await seed_events(sessionmaker, ctx, 1, "回答一")
    await seed_events(sessionmaker, ctx, 2, "回答二")
    async with sessionmaker() as session, session.begin():
        await session.execute(KnowledgeSearchLog.__table__.delete().where(
            KnowledgeSearchLog.id == f"{ctx.workspace_id}-log1"))
        old = await session.get(RunSummary, (f"{ctx.workspace_id}-c1", 2))
        old.started_at = datetime.now(UTC) - timedelta(days=RERATE_DAYS + 1)
    assert await requeue_rated(sessionmaker, ctx.workspace_id, f"{ctx.workspace_id}-c1", 1) is False
    assert await requeue_rated(sessionmaker, ctx.workspace_id, f"{ctx.workspace_id}-c1", 2) is False
    assert await pending(sessionmaker, ctx) == []


async def test_rating_a_turn_down_or_taking_the_dislike_back_tells_the_checker(sessionmaker):
    """评分的存取：点了踩、改了踩的说明、把踩撤回或改成赞，都通知一次（重新判断这一轮）；只点赞、撤回赞不通知"""
    ctx = await seed(sessionmaker, 1)
    told: list[tuple[str, str, int]] = []

    async def on_dislike(workspace_id: str, conversation_id: str, turn: int) -> None:
        told.append((workspace_id, conversation_id, turn))

    store = FeedbackStore(sessionmaker, on_dislike=on_dislike)
    async with sessionmaker() as session:
        conversation = await session.get(Conversation, f"{ctx.workspace_id}-c1")
    await store.put(conversation, "run-1", "like")
    await store.delete(conversation, "run-1")
    assert told == []
    await store.put(conversation, "run-1", "dislike", note="不对")
    await store.put(conversation, "run-1", "like")
    await store.put(conversation, "run-1", "dislike")
    await store.delete(conversation, "run-1")
    assert told == [(ctx.workspace_id, f"{ctx.workspace_id}-c1", 1)] * 4
