"""回合结束时告诉扩展的知识源：这一轮的回答引用了它的哪几份文档、是谁问的（挂在每轮汇总的回合结束上）。

只给声明了 cited 能力的扩展知识源（例如按人记常用资料，检索时给常用的加一点分）。按回答里标的编号
（marked_numbers：[n] 与放进回答的图 [IMG:n:k]）查这个会话的引用目录，同一份文档只给一次。没答完的、
不知道是谁问的、没引用的不给。一个知识源出错只记日志，不影响别的，也不影响运行记录。
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..access.rls import set_workspace
from ..conversations.summaries import TurnEnded
from .citations import marked_numbers
from .models import Caller, CitedDocument
from .provided import ProvidedSources
from .tables import Citation

log = logging.getLogger(__name__)
FEATURE = "cited"


class CitationNotifier:
    def __init__(self, sessionmaker: async_sessionmaker, provided: ProvidedSources) -> None:
        self._sessionmaker = sessionmaker
        self._provided = provided

    async def turn_ended(self, ended: TurnEnded) -> None:
        listeners = {item.id: item.source for item in self._provided.all() if FEATURE in item.source.features}
        marked = marked_numbers(ended.answer)
        if not listeners or not marked or ended.status != "completed" or not ended.user_id:
            return
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, ended.workspace_id)
            rows = (await session.execute(select(Citation.source_id, Citation.doc_ref, Citation.title).where(
                Citation.catalog_id == ended.conversation_id, Citation.number.in_(sorted(marked)),
                Citation.source_id.in_(tuple(listeners))).order_by(Citation.number))).all()
        by_source: dict[str, dict[str, CitedDocument]] = {}
        for source_id, doc_ref, title in rows:
            by_source.setdefault(source_id, {}).setdefault(doc_ref, CitedDocument(doc_ref, title))
        caller = Caller(workspace_id=ended.workspace_id, user_id=ended.user_id)
        for source_id, documents in by_source.items():
            try:
                await listeners[source_id].cited(caller, tuple(documents.values()))
            except Exception:
                log.exception("telling %s which documents %s cited failed", source_id, ended.conversation_id)
        return
