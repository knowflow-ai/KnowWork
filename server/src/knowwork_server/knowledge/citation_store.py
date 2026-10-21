"""引用目录落库。并发安全：同一目录的注册在目录头行上加行锁，编号不会重复。"""

from collections.abc import Iterable

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..access.rls import set_workspace
from .citations import DEFAULT_LIMIT, CitationCatalog, CitationCheck, CitationEntry
from .models import Evidence, Locator
from .tables import Citation, CitationCatalogRow


class CitationStore:
    def __init__(self, sessionmaker: async_sessionmaker, *, limit: int = DEFAULT_LIMIT) -> None:
        self._sessionmaker = sessionmaker
        self._limit = limit

    async def register(self, workspace_id: str, catalog_id: str,
                       evidences: Iterable[Evidence]) -> tuple[tuple[int | None, ...], CitationCatalog]:
        items = list(evidences)
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            await session.execute(insert(CitationCatalogRow).values(
                id=catalog_id, workspace_id=workspace_id, size=0, limit=self._limit, truncated=False, dropped=0,
            ).on_conflict_do_nothing(index_elements=["id"]))
            head = await session.scalar(select(CitationCatalogRow).where(CitationCatalogRow.id == catalog_id)
                                        .with_for_update())
            if head is None:  # 目录属于别的空间：行级安全挡住了
                raise PermissionError("citation catalog belongs to another workspace")
            before = await _load(session, head)
            after, numbers = before.register(items)
            session.add_all(_row(workspace_id, catalog_id, entry) for entry in after.entries[len(before.entries):])
            head.size, head.truncated, head.dropped = len(after.entries), after.truncated, after.dropped
        return numbers, after

    async def discard(self, workspace_id: str, catalog_id: str) -> None:
        """删除整个引用目录（条目随外键级联删除）。"""
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            await session.execute(delete(CitationCatalogRow).where(CitationCatalogRow.id == catalog_id))

    async def load(self, workspace_id: str, catalog_id: str) -> CitationCatalog:
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            head = await session.get(CitationCatalogRow, catalog_id)
            if head is None:
                return CitationCatalog(limit=self._limit)
            return await _load(session, head)

    async def check_answer(self, workspace_id: str, catalog_id: str, answer: str) -> CitationCheck:
        """校验最终回答里的 [n]：去掉不存在的编号，警告交给调用方记进执行过程。"""
        return (await self.load(workspace_id, catalog_id)).check(answer)


async def _load(session: AsyncSession, head: CitationCatalogRow) -> CitationCatalog:
    rows = (await session.scalars(select(Citation).where(Citation.catalog_id == head.id)
                                  .order_by(Citation.number))).all()
    entries = tuple(CitationEntry(row.number, _evidence(row)) for row in rows)
    return CitationCatalog(entries=entries, limit=head.limit, truncated=head.truncated, dropped=head.dropped)


def _evidence(row: Citation) -> Evidence:
    locator = Locator.model_validate(row.locator) if row.locator else None
    return Evidence(source_id=row.source_id, doc_ref=row.doc_ref, span_ref=row.span_ref, title=row.title,
                    text=row.text, locator=locator, open_url=row.open_url)


def _row(workspace_id: str, catalog_id: str, entry: CitationEntry) -> Citation:
    ev = entry.evidence
    return Citation(catalog_id=catalog_id, workspace_id=workspace_id, number=entry.number, source_id=ev.source_id,
                    doc_ref=ev.doc_ref[:300], span_ref=ev.span_ref[:300], title=ev.title[:500], text=ev.text,
                    locator=ev.locator.model_dump(mode="json") if ev.locator else None,
                    open_url=(ev.open_url or None) and ev.open_url[:1000])
