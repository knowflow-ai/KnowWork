"""聊天里每次知识检索查了什么、命中哪些片段、回答用没用上（知识库运营的原始信号）。

- 检索时记（knowledge_search 调 record）：一次检索一条，命中按名次一行，带它在引用目录里的编号；第几轮从每轮汇总
  那边取（conversations.summaries.RunSummaries.current_turn），没看到这一轮开始的（进程刚重启、多副本时检索落到
  别的副本）先空着；
- 回合结束时收尾（close_turn 登记在每轮汇总上，作为「knowledge」这一项事实）：这一轮的检索补上轮次号，按回答里的
  [n] 和放的图标出被引用的命中，交回检索了几次、引用了几条。这一轮没检索过就不给；
- 检索过、正常答完的一轮记一条待判断（answer_check）：后台每小时一批用模型判断知识库答上了没有，结论晚些并进
  知识事实（verdict、missing、reason）；
- 记录失败只写日志，不影响检索和回答。删会话时一起删（discard）；
- 检索时一起记下这是哪次工具调用（callId）和知识源交回的检索过程，运行详情按 callId 取出来（traces），显示在
  知识检索那一步下面。检索过程不给模型。
"""

import logging
import uuid
from collections.abc import Callable, Sequence

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..access.rls import set_workspace
from ..conversations.summaries import TurnEnded
from ..tools.registry import ToolContext
from .answer_check import queue_check
from .citations import marked_numbers
from .models import Evidence, SearchTraceEntry
from .tables import Citation, KnowledgeAnswerCheck, KnowledgeSearchHit, KnowledgeSearchLog

log = logging.getLogger(__name__)

CurrentTurn = Callable[[str], int | None]


def _unknown(_conversation_id: str) -> int | None:
    return None


class SearchLog:
    def __init__(self, sessionmaker: async_sessionmaker, current_turn: CurrentTurn = _unknown) -> None:
        self._sessionmaker = sessionmaker
        self._current_turn = current_turn

    async def record(self, ctx: ToolContext, *, query: str, options: dict, evidence: Sequence[Evidence],
                     numbers: Sequence[int | None], trace: Sequence[SearchTraceEntry] = ()) -> None:
        """记一次检索。子 Agent 的检索也记在根会话这一轮上（ctx.session_id 是根会话）。"""
        log_id = f"ksl_{uuid.uuid4().hex}"
        try:
            async with self._sessionmaker() as session, session.begin():
                await set_workspace(session, ctx.workspace_id)
                session.add(KnowledgeSearchLog(
                    id=log_id, workspace_id=ctx.workspace_id, conversation_id=ctx.session_id,
                    turn=self._current_turn(ctx.session_id), user_id=ctx.user_id, query=query[:1000],
                    options=options, call_id=ctx.call_id,
                    trace=[entry.model_dump(mode="json") for entry in trace] or None))
                await session.flush()  # 命中对检索记录有外键，先把它写进去
                session.add_all(KnowledgeSearchHit(
                    log_id=log_id, rank=rank, workspace_id=ctx.workspace_id, number=number, source_id=item.source_id,
                    kb_id=item.kb_id[:64], doc_ref=item.doc_ref[:500], span_ref=item.span_ref[:500],
                    title=item.title[:500], score=item.score, cited=False)
                    for rank, (item, number) in enumerate(zip(evidence, numbers, strict=True)))
        except Exception:
            log.exception("recording knowledge search in %s failed", ctx.session_id)

    async def close_turn(self, ended: TurnEnded) -> dict | None:
        """这一轮的检索补上轮次号、标出被引用的命中；交回检索次数 searches、引用条数 cited、命中过的知识库 bases、
        被引用的知识库 cited_bases（知识库都是全局 id <知识源id>.<知识库id>，问答大盘按库切分用）。这一轮没检索就不给；
        检索过、正常答完的记一条待判断（答上没有晚些由 answer_check 补进来）。

        引用了几条按会话的引用目录算：回答也会引用前几轮查到的、或者读文档时登记的片段。"""
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, ended.workspace_id)
            log_ids = (await session.scalars(select(KnowledgeSearchLog.id).where(
                KnowledgeSearchLog.conversation_id == ended.conversation_id,
                or_(KnowledgeSearchLog.turn == ended.turn, KnowledgeSearchLog.turn.is_(None))))).all()
            if not log_ids:
                return None
            if ended.status == "completed":
                await queue_check(session, ended)
            await session.execute(update(KnowledgeSearchLog).where(
                KnowledgeSearchLog.id.in_(log_ids), KnowledgeSearchLog.turn.is_(None)).values(turn=ended.turn))
            marked = marked_numbers(ended.answer)
            cited = 0
            if marked:
                await session.execute(update(KnowledgeSearchHit).where(
                    KnowledgeSearchHit.log_id.in_(log_ids), KnowledgeSearchHit.number.in_(marked)).values(cited=True))
                cited = await session.scalar(select(func.count()).select_from(Citation).where(
                    Citation.catalog_id == ended.conversation_id, Citation.number.in_(marked))) or 0
            hit = (await session.execute(select(KnowledgeSearchHit.source_id, KnowledgeSearchHit.kb_id,
                                                func.bool_or(KnowledgeSearchHit.cited))
                                         .where(KnowledgeSearchHit.log_id.in_(log_ids), KnowledgeSearchHit.kb_id != "")
                                         .group_by(KnowledgeSearchHit.source_id, KnowledgeSearchHit.kb_id))).all()
        facts = {"searches": len(log_ids), "cited": cited, "bases": sorted(f"{source}.{kb}" for source, kb, _ in hit),
                 "cited_bases": sorted(f"{source}.{kb}" for source, kb, used in hit if used)}
        return facts

    async def traces(self, workspace_id: str, conversation_id: str,
                     call_ids: Sequence[str]) -> dict[str, list[dict]]:
        """运行详情用：这些工具调用各自的检索过程（没记过、记录里没有过程的不给）。"""
        if not call_ids:
            return {}
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            rows = (await session.execute(select(KnowledgeSearchLog.call_id, KnowledgeSearchLog.trace).where(
                KnowledgeSearchLog.conversation_id == conversation_id,
                KnowledgeSearchLog.call_id.in_(list(call_ids))))).all()
        return {call_id: list(trace) for call_id, trace in rows if call_id and trace}

    async def discard(self, workspace_id: str, conversation_id: str) -> None:
        """删会话时一起删（命中随检索记录级联删除）。"""
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            await session.execute(delete(KnowledgeSearchLog).where(
                KnowledgeSearchLog.conversation_id == conversation_id))
            await session.execute(delete(KnowledgeAnswerCheck).where(
                KnowledgeAnswerCheck.conversation_id == conversation_id))

