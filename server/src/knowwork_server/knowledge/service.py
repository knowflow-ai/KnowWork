"""运行时的知识检索与阅读：按调用者身份查一个或多个知识源，结果用 RRF 合并（技术方案 5.4、8.1 节）。

范围分两层收窄（第 5.4 节）：
1. 这里先算：知识源的开放范围 ∩ 授权给调用者的知识库 ∩ 本轮选择（或模型点名的知识库）。点名了不该查的知识库，
   这个来源直接拒绝，不发给知识源；
2. 连接器带着调用者身份去查，KnowFlow 再按它自己的权限收窄（统一账号方式下只有第 1 层）。

失败一律说明原因，不静默变成「没有知识」：
- 本轮绑定了知识（或空间里配置了知识），但调用者一个都读不了 → NoReadableKnowledge，message 列出每个源的原因；
- 部分知识源读不了 → 照常返回能读到的结果，并在 notes 里写明哪些源因为什么被跳过；
- 全部因为知识源暂时不可用 → SourceUnavailable。
"""

import asyncio
import logging
from dataclasses import dataclass

from .bases import RESOURCE as BASE_RESOURCE
from .bases import Grants, Selection, admit, base_id, candidates
from .connectors import AccessChecker, ConnectorFactory, EmailLookup, parse_config
from .errors import (
    AccessDenied,
    InvalidInput,
    KnowledgeError,
    NoReadableKnowledge,
    NotFound,
    ScopeDenied,
    SourceUnavailable,
)
from .models import MAX_READ_CHARS, Caller, Cursor, DocumentPage, Evidence, KnowledgeSource, Page, ReadResult, Scope
from .rrf import rrf_merge
from .store import SourceRecord, SourceStore

log = logging.getLogger(__name__)

MAX_TOP_N = 20
MAX_SOURCES_PER_TURN = 10


@dataclass(frozen=True)
class SearchOutcome:
    evidence: tuple[Evidence, ...]
    notes: tuple[str, ...] = ()  # 被跳过的知识源及原因，要交给模型与用户


class KnowledgeService:
    def __init__(self, store: SourceStore, access: AccessChecker, connectors: ConnectorFactory,
                 email_of: EmailLookup) -> None:
        self._store = store
        self._access = access
        self._connectors = connectors
        self._email_of = email_of

    async def caller(self, workspace_id: str, user_id: str) -> Caller:
        return Caller(workspace_id=workspace_id, user_id=user_id, email=await self._email_of(user_id))

    async def search(self, caller: Caller, query: str, *, top_n: int = 8,
                     bases: tuple[str, ...] | None = None) -> SearchOutcome:
        """bases 是要查的知识库（全局 id 或 <知识源id>.*）；None 表示本人能查的全部。"""
        query = (query or "").strip()
        if not 1 <= len(query) <= 1000:
            raise InvalidInput("检索词长度须为 1 到 1000 个字符")
        top_n = max(1, min(top_n, MAX_TOP_N))
        selection = Selection.of(bases) if bases is not None else None
        grants = self._grants(caller)
        usable, skipped = await self._usable(caller, grants, selection)
        results = await asyncio.gather(*(self._search_one(caller, r, grants, selection, query, top_n) for r in usable))
        rankings = [hits for hits in results if not isinstance(hits, _Failure)]
        failures = skipped + [hits for hits in results if isinstance(hits, _Failure)]
        if not rankings:
            raise _nothing_readable(failures)
        return SearchOutcome(evidence=tuple(rrf_merge(rankings, top_n=top_n)),
                             notes=tuple(f.text for f in failures))

    async def read(self, caller: Caller, source_id: str, doc_ref: str, cursor: str | None = None,
                   max_chars: int = MAX_READ_CHARS, *, bases: tuple[str, ...] | None = None) -> ReadResult:
        """文档所在的知识库要在开放范围内、授权给了调用者，并且在本轮选择（bases）里。"""
        record = await self._record(caller, source_id)
        try:
            position = Cursor.decode(cursor) if cursor else None
        except ValueError as error:
            raise InvalidInput(str(error)) from error
        source = self._connectors.build(record)
        kb = source.base_of(doc_ref)
        configured = parse_config(record).dataset_ids
        if not admit(source_id, (kb,), configured, self._grants(caller))[0]:
            raise NotFound("文档不存在或你没有使用权限")
        if bases is not None and not Selection.of(bases).covers(source_id, kb):
            raise ScopeDenied("这份文档所在的知识库不在本轮选择的知识范围内")
        budget = max(1, min(max_chars, MAX_READ_CHARS))
        return await source.read(caller, doc_ref, position, budget)

    async def list_documents(self, caller: Caller, source_id: str, page: Page) -> DocumentPage:
        record = await self._record(caller, source_id)
        source = self._connectors.build(record)
        datasets = await self._datasets(caller, record, source, self._grants(caller), None)
        return await source.list_documents(caller, Scope(dataset_ids=datasets), page)

    def _grants(self, caller: Caller) -> Grants:
        return Grants(self._access.visible_resources(caller.user_id, caller.workspace_id, BASE_RESOURCE, "use"))

    async def _record(self, caller: Caller, source_id: str) -> SourceRecord:
        if not self._grants(caller).any_in(source_id):
            raise NotFound()
        record = await self._store.get(caller.workspace_id, source_id)
        if not record.enabled:
            raise AccessDenied(f"知识源「{record.name}」已停用")
        return record

    async def _usable(self, caller: Caller, grants: Grants,
                      selection: Selection | None) -> tuple[list[SourceRecord], list["_Failure"]]:
        """要查的知识源。点名了的逐个说明为什么不能查；没点名时只看授权了知识库的来源，其余不提。"""
        if selection is not None and not selection.by_source:
            raise NoReadableKnowledge("本轮没有选择知识库")
        records = {r.id: r for r in await self._store.list(caller.workspace_id)}
        if not records:
            raise NoReadableKnowledge("本空间还没有配置知识源")
        wanted = tuple(selection.by_source) if selection else tuple(r for r in records if grants.any_in(r))
        if not wanted:
            raise NoReadableKnowledge("本空间的知识库你都没有使用权限，请联系管理员授权")
        usable: list[SourceRecord] = []
        skipped: list[_Failure] = []
        for source_id in wanted:
            record = records.get(source_id)
            if record is None or not grants.any_in(source_id):
                skipped.append(_Failure(f"知识源 {source_id}：不存在或你没有使用权限"))
            elif not record.enabled:
                skipped.append(_Failure(f"知识源「{record.name}」：已停用"))
            else:
                usable.append(record)
        if len(usable) > MAX_SOURCES_PER_TURN:
            raise InvalidInput(f"一次最多查询 {MAX_SOURCES_PER_TURN} 个知识源，请指定要查的知识库")
        return usable, skipped

    async def _datasets(self, caller: Caller, record: SourceRecord, source: KnowledgeSource, grants: Grants,
                        picked: frozenset[str] | None) -> tuple[str, ...]:
        """第 1 层收窄后要查的知识库。开放范围留空、授权覆盖整个来源时，以调用者身份列出他读得到的再筛。"""
        configured = parse_config(record).dataset_ids
        listed = candidates(record.id, configured, grants, picked)
        if listed is None:
            listed = tuple(base.id for base in await source.list_bases(caller))
        kept, refused = admit(record.id, listed, configured, grants)
        if refused and picked is not None:
            raise ScopeDenied(f"知识库 {base_id(record.id, refused[0])} 不存在或你没有使用权限")
        if not kept:
            raise ScopeDenied("这里没有你能查的知识库")
        return kept

    async def _search_one(self, caller: Caller, record: SourceRecord, grants: Grants, selection: Selection | None,
                          query: str, top_n: int) -> "list[Evidence] | _Failure":
        picked = selection.by_source[record.id] if selection else None
        try:
            source = self._connectors.build(record)
            datasets = await self._datasets(caller, record, source, grants, picked)
            return await source.search(caller, query, Scope(dataset_ids=datasets), top_n)
        except KnowledgeError as error:
            return _Failure(f"知识源「{record.name}」：{error}", transient=isinstance(error, SourceUnavailable))
        except Exception:  # 连接器内部错误只记日志
            log.exception("knowledge source %s search failed", record.id)
            return _Failure(f"知识源「{record.name}」：暂时不可用", transient=True)


@dataclass(frozen=True)
class _Failure:
    text: str
    transient: bool = False  # 知识源暂时不可用（而不是调用者没有权限或身份）


def _nothing_readable(failures: list[_Failure]) -> KnowledgeError:
    detail = "；".join(f.text for f in failures)
    if failures and all(f.transient for f in failures):
        return SourceUnavailable(f"知识源暂时不可用：{detail}")
    return NoReadableKnowledge(f"本轮绑定的知识你一个都读不了：{detail}")
