"""搜索本人的对话（侧栏的搜索，照 ChatGPT）：按标题和每一轮用户问的话找，最近有动静的在前，每个对话一条，
带一小段命中的原话（前后各留一点上下文），界面里把命中的词标出来。

回答的正文不搜：回答只在会话日志（run_event）的事件帧里，没有单独存成可搜的文字；每轮问的话在每轮汇总
（run_summary.question）里有。只有标题命中的，片段为空。
"""

from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from .models import Conversation, RunSummary

MAX_RESULTS = 20
MAX_QUERY = 100
CONTEXT = 24  # 片段里命中处前后各留几个字


@dataclass(frozen=True)
class SearchHit:
    conversation: Conversation
    snippet: str  # 命中的那句问话的一段；只有标题命中时为空


def _like(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def snippet(text: str, query: str) -> str:
    """命中处前后各留 CONTEXT 个字，截掉的地方加省略号；空白压成一个空格。"""
    flat = " ".join(text.split())
    at = flat.lower().find(query.lower())
    if at < 0:
        return flat[:CONTEXT * 2]
    start, end = max(0, at - CONTEXT), min(len(flat), at + len(query) + CONTEXT)
    return f"{'…' if start else ''}{flat[start:end]}{'…' if end < len(flat) else ''}"


async def search_conversations(sessionmaker: async_sessionmaker, *, workspace_id: str, user_id: str,
                               query: str) -> tuple[SearchHit, ...]:
    text = " ".join(query.split())[:MAX_QUERY]
    if not text:
        return ()
    pattern = _like(text)
    async with sessionmaker() as session:
        rows = (await session.execute(
            select(Conversation, RunSummary.question)
            .outerjoin(RunSummary, (RunSummary.conversation_id == Conversation.id)
                       & RunSummary.question.ilike(pattern, escape="\\"))
            .where(Conversation.workspace_id == workspace_id, Conversation.user_id == user_id,
                   or_(Conversation.title.ilike(pattern, escape="\\"), RunSummary.question.is_not(None)))
            .order_by(Conversation.updated_at.desc(), Conversation.id.desc(), RunSummary.turn.desc())
            .limit(MAX_RESULTS * 5))).all()
    hits: dict[str, SearchHit] = {}
    for conversation, question in rows:
        if conversation.id not in hits:  # 一个对话一条：取最近一轮命中的问话
            hits[conversation.id] = SearchHit(conversation, snippet(question, text) if question else "")
        if len(hits) == MAX_RESULTS:
            break
    return tuple(hits.values())
