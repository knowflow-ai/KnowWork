"""对一轮回答的赞或踩（照 DSH 官方的消息反馈）：评分必填，分类与说明可选，再点一次已选的评分就撤回。

分类照 DSH 官方 dsh-command-feedback 的固定分类表（键名一致，以后要和官方会话日志互通不用换算）；界面上的中文由前端给。
只有会话的主人能评（接口先按会话判权）。

点踩会让这一轮重新判断「知识库答上了没有」（knowledge/answer_check.py 的 requeue_rated，装配时给 on_dislike）：
点了踩、改了踩的说明、把踩撤回或改成赞，都通知一次；只点赞、撤回赞不通知。通知出错只记日志，评分照样存下。
"""

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from .models import AnswerFeedback, Conversation

log = logging.getLogger(__name__)

RATINGS = ("like", "dislike")
OnDislike = Callable[[str, str, int], Awaitable[object]]  # (空间, 会话, 第几轮)
CATEGORIES = ("task-result", "instruction-following", "product-interaction", "service-stability", "resource-cost",
              "security-privacy-permission", "other")
MAX_NOTE = 2000
RUN_ID = re.compile(r"^run-\d{1,9}$")


@dataclass(frozen=True)
class Feedback:
    run_id: str
    rating: str
    category: str | None
    note: str | None
    updated_at: datetime | None


def _view(row: AnswerFeedback) -> Feedback:
    return Feedback(run_id=row.run_id, rating=row.rating, category=row.category, note=row.note,
                    updated_at=row.updated_at)


class FeedbackStore:
    def __init__(self, sessionmaker: async_sessionmaker, *, on_dislike: OnDislike | None = None) -> None:
        self._sessionmaker = sessionmaker
        self._on_dislike = on_dislike

    async def _rated(self, conversation: Conversation, run_id: str, before: str | None, after: str | None) -> None:
        """点了踩、改了踩，或者原来是踩、现在不是了：通知一次（这一轮重新判断答上没有）。"""
        if self._on_dislike is None or "dislike" not in (before, after):
            return
        try:
            await self._on_dislike(conversation.workspace_id, conversation.id, int(run_id.removeprefix("run-")))
        except Exception:
            log.warning("re-judging %s %s after a rating change failed", conversation.id, run_id, exc_info=True)

    async def _rating(self, conversation: Conversation, run_id: str) -> str | None:
        async with self._sessionmaker() as session:
            return await session.scalar(select(AnswerFeedback.rating).where(
                AnswerFeedback.conversation_id == conversation.id, AnswerFeedback.run_id == run_id))

    async def list(self, conversation: Conversation) -> tuple[Feedback, ...]:
        async with self._sessionmaker() as session:
            rows = (await session.scalars(select(AnswerFeedback).where(
                AnswerFeedback.conversation_id == conversation.id).order_by(AnswerFeedback.run_id))).all()
        return tuple(_view(row) for row in rows)

    async def put(self, conversation: Conversation, run_id: str, rating: str, category: str | None = None,
                  note: str | None = None) -> Feedback:
        """记下（或改成）这一轮的评分；调用方已校验 run_id、评分、分类的取值。"""
        text = (note or "").strip()[:MAX_NOTE] or None
        values = {"rating": rating, "category": category, "note": text, "updated_at": func.now()}
        before = await self._rating(conversation, run_id)
        async with self._sessionmaker() as session, session.begin():
            row = await session.scalar(insert(AnswerFeedback).values(
                conversation_id=conversation.id, run_id=run_id, workspace_id=conversation.workspace_id,
                user_id=conversation.user_id, **values)
                .on_conflict_do_update(index_elements=["conversation_id", "run_id"], set_=values)
                .returning(AnswerFeedback))
            saved = _view(row)
        await self._rated(conversation, run_id, before, rating)
        return saved

    async def delete(self, conversation: Conversation, run_id: str) -> None:
        before = await self._rating(conversation, run_id)
        async with self._sessionmaker() as session, session.begin():
            await session.execute(delete(AnswerFeedback).where(AnswerFeedback.conversation_id == conversation.id,
                                                               AnswerFeedback.run_id == run_id))
        await self._rated(conversation, run_id, before, None)
