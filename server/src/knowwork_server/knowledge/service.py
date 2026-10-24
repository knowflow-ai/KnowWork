"""运行时的知识检索与阅读：按调用者身份查一个或多个知识源，结果用 RRF 合并。

范围分两层收窄（第 5.4 节）：
1. 这里先算：知识源的开放范围 ∩ 授权给调用者的知识库 ∩ 本轮选择（或模型点名的知识库）。点名了不该查的知识库，
   这个来源直接拒绝，不发给知识源；
2. 连接器带着调用者身份去查，KnowFlow 再按它自己的权限收窄（统一账号方式下只有第 1 层）。

可选参数（检索方式、文件夹、标签、元数据条件；按节读、从片段读、文档内查找）各知识源支持的不一样，
知识源在 features 里声明。不支持的这里去掉，并在 notes 里写明「已忽略」，模型知道结果没按那个条件筛过。

失败一律说明原因，不静默变成「没有知识」：
- 本轮绑定了知识（或空间里配置了知识），但调用者一个都读不了 → NoReadableKnowledge，message 列出每个源的原因；
- 部分知识源读不了 → 照常返回能读到的结果，并在 notes 里写明哪些源因为什么被跳过；
- 全部因为知识源暂时不可用 → SourceUnavailable。
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import get_args

from pydantic import ValidationError

from .bases import ALL, Grants, Selection, admit, base_id, candidates, parse_selector
from .bases import RESOURCE as BASE_RESOURCE
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
from .models import (
    MAX_READ_CHARS,
    BaseInfo,
    Caller,
    Cursor,
    DocumentPage,
    Evidence,
    KnowledgeSource,
    MetaFilter,
    Page,
    Pin,
    ReadRequest,
    ReadResult,
    Scope,
    SearchHits,
    SearchMode,
    SearchRequest,
    expand_parts,
)
from .provided import ProvidedSources
from .rrf import rrf_merge
from .store import SourceRecord, SourceStore

log = logging.getLogger(__name__)

MAX_TOP_N = 20
MAX_SOURCES_PER_TURN = 10
FEATURE_NAMES = {"mode": "指定检索方式", "folders": "按文件夹检索", "tags": "按标签检索", "filters": "按元数据条件检索",
                 "outline": "查看目录", "section": "按章节阅读", "chunk": "从指定片段读", "find": "在文档内查找"}
# 知识源不支持时，把这次请求里对应的参数恢复成默认值
_SEARCH_DEFAULTS = {"mode": "hybrid", "folders": (), "tags": (), "filters": ()}
_READ_DEFAULTS = {"outline": False, "section": None, "chunk": None, "find": None}


@dataclass(frozen=True)
class SearchOptions:
    """检索的可选条件，原样传给支持它们的知识源。"""

    mode: str = "hybrid"
    folders: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    filters: tuple[MetaFilter, ...] = ()


@dataclass(frozen=True)
class ReadOptions:
    """阅读方式：三选一，都不给就从游标处顺序读。chunk 是片段引用（span_ref）。"""

    section: str | None = None
    chunk: str | None = None
    context: int = 1
    find: str | None = None
    outline: bool = False  # 看目录；可以配 section 只看那一章下面的


def _unsupported(source: KnowledgeSource, wanted: frozenset[str]) -> frozenset[str]:
    return wanted - frozenset(getattr(source, "features", frozenset()))


def _ignored_note(name: str, missing: frozenset[str]) -> str:
    what = "、".join(label for key, label in FEATURE_NAMES.items() if key in missing)
    return f"知识源「{name}」不支持{what}，这些条件已忽略"


@dataclass(frozen=True)
class ListedBase:
    id: str            # 全局 id：<知识源>.<知识库>
    source_name: str
    filterable: bool   # 知识源支持按字段、标签筛选
    info: BaseInfo
    pinnable: bool = False  # 知识源支持 @ 点名文件、标签（检索范围里的 pins）


@dataclass(frozen=True)
class FoundDocument:
    """按名字找到的一份文档（@ 菜单搜文件）：base 是所在知识库的全局 id。"""

    source_id: str
    doc_ref: str
    title: str
    base: str
    base_name: str


@dataclass(frozen=True)
class Pinned:
    """用户 @ 点名的一个知识库里的文档与标签：base 是全局 id，documents 是知识源给的文档引用（doc_ref）。"""

    base: str
    documents: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class BaseListing:
    bases: tuple[ListedBase, ...]
    notes: tuple[str, ...] = ()  # 列不出来的知识源及原因


@dataclass(frozen=True)
class SearchOutcome:
    evidence: tuple[Evidence, ...]
    notes: tuple[str, ...] = ()  # 被跳过的知识源及原因，要交给模型与用户


class KnowledgeService:
    def __init__(self, store: SourceStore, access: AccessChecker, connectors: ConnectorFactory,
                 email_of: EmailLookup, provided: ProvidedSources | None = None) -> None:
        self._store = store
        self._access = access
        self._connectors = connectors
        self._email_of = email_of
        self._provided = provided or ProvidedSources()

    def _build(self, record: SourceRecord) -> KnowledgeSource:
        provided = self._provided.get(record.id)
        return provided.source if provided else self._connectors.build(record)

    def _open_range(self, record: SourceRecord) -> tuple[str, ...]:
        """知识源的开放范围；扩展提供的知识源开放它自己的全部知识库（空元组同「不限」）。"""
        return () if self._provided.get(record.id) else parse_config(record).dataset_ids

    async def _records(self, workspace_id: str) -> dict[str, SourceRecord]:
        records = {r.id: r for r in await self._store.list(workspace_id)}
        return {**records, **{p.id: p.record(workspace_id) for p in self._provided.all()}}

    async def caller(self, workspace_id: str, user_id: str) -> Caller:
        return Caller(workspace_id=workspace_id, user_id=user_id, email=await self._email_of(user_id))

    async def search(self, caller: Caller, query: str, *, top_n: int = 8,
                     bases: tuple[str, ...] | None = None, options: SearchOptions | None = None,
                     pins: tuple[Pinned, ...] = ()) -> SearchOutcome:
        """bases 是要查的知识库（全局 id 或 <知识源id>.*）；None 表示本人能查的全部。
        pins 是用户 @ 点名的文件与标签：点名的知识库只查它们（知识库本身也要在 bases 里，运行配置已并进去）。"""
        query = (query or "").strip()
        if not 1 <= len(query) <= 1000:
            raise InvalidInput("检索词长度须为 1 到 1000 个字符")
        options = options or SearchOptions()
        if options.mode not in get_args(SearchMode):
            raise InvalidInput("检索方式只能是 hybrid、semantic 或 keyword")
        top_n = max(1, min(top_n, MAX_TOP_N))
        selection = Selection.of(bases) if bases is not None else None
        grants = self._grants(caller)
        usable, skipped = await self._usable(caller, grants, selection)
        results = await asyncio.gather(*(self._search_one(caller, r, grants, selection, query, top_n, options, pins)
                                         for r in usable))
        rankings = [hits for hits, _ in results if not isinstance(hits, _Failure)]
        failures = skipped + [hits for hits, _ in results if isinstance(hits, _Failure)]
        if not rankings:
            raise _nothing_readable(failures)
        ignored = [note for _, notes in results for note in notes]
        # 条数按知识源给的整条算，截断之后再把合并过的拆回一块一块
        return SearchOutcome(evidence=expand_parts(rrf_merge(rankings, top_n=top_n)),
                             notes=tuple(f.text for f in failures) + tuple(ignored))

    async def bases(self, caller: Caller, *, bases: tuple[str, ...] | None = None) -> BaseListing:
        """本人此刻能查的知识库（范围收窄同检索），带名字、描述、字段定义。给每轮的知识库目录与 knowledge_bases 工具；
        一个知识源列不出来只记说明，不影响别的。"""
        selection = Selection.of(bases) if bases is not None else None
        grants = self._grants(caller)
        try:
            usable, skipped = await self._usable(caller, grants, selection)
        except KnowledgeError as error:
            return BaseListing((), (str(error),))
        listed = await asyncio.gather(*(self._list_one(caller, r, grants, selection) for r in usable))
        found = tuple(base for result in listed if not isinstance(result, _Failure) for base in result)
        failures = skipped + [result for result in listed if isinstance(result, _Failure)]
        return BaseListing(found, tuple(f.text for f in failures))

    async def _list_one(self, caller: Caller, record: SourceRecord, grants: Grants,
                        selection: Selection | None) -> "list[ListedBase] | _Failure":
        picked = selection.by_source[record.id] if selection else None
        try:
            source = self._build(record)
            listed = await source.list_bases(caller)
        except KnowledgeError as error:
            return _Failure(f"知识源「{record.name}」：{error}")
        except Exception:
            log.exception("listing bases of knowledge source %s failed", record.id)
            return _Failure(f"知识源「{record.name}」暂时不可用", transient=True)
        kept, _ = admit(record.id, (b.id for b in listed), self._open_range(record), grants)
        features = frozenset(getattr(source, "features", frozenset()))
        filterable = {"filters", "tags"} <= features
        by_id = {b.id: b for b in listed}
        return [ListedBase(base_id(record.id, kb), record.name, filterable, by_id[kb], pinnable="pins" in features)
                for kb in kept if picked is None or kb in picked]

    async def find_documents(self, caller: Caller, query: str, *, limit: int = 20) -> tuple[FoundDocument, ...]:
        """按名字找本人能查的文档（@ 菜单搜文件）；没给名字时列最近更新的。只问声明了 document_query 的知识源；
        找不到、出错都只是少几条。"""
        query = " ".join((query or "").split())[:100]
        grants = self._grants(caller)
        try:
            usable, _ = await self._usable(caller, grants, None)
        except KnowledgeError:
            return ()
        found = await asyncio.gather(*(self._find_in(caller, r, grants, query, limit) for r in usable))
        return tuple(doc for docs in found for doc in docs)[:limit]

    async def _find_in(self, caller: Caller, record: SourceRecord, grants: Grants, query: str,
                       limit: int) -> list[FoundDocument]:
        try:
            source = self._build(record)
            if "document_query" not in getattr(source, "features", frozenset()):
                return []
            names = {b.id: b.name for b in await source.list_bases(caller)}
            kept, _ = admit(record.id, names, self._open_range(record), grants)
            if not kept:
                return []
            page = await source.list_documents(caller, Scope(dataset_ids=kept), Page(query=query, size=limit))
            return [FoundDocument(record.id, d.doc_ref, d.title, base_id(record.id, kb), names.get(kb, kb))
                    for d in page.items if (kb := source.base_of(d.doc_ref)) in kept]
        except Exception:
            log.warning("finding documents in knowledge source %s failed", record.id, exc_info=True)
            return []

    async def read(self, caller: Caller, source_id: str, doc_ref: str, cursor: str | None = None,
                   max_chars: int = MAX_READ_CHARS, *, bases: tuple[str, ...] | None = None,
                   options: ReadOptions | None = None) -> ReadResult:
        """文档所在的知识库要在开放范围内、授权给了调用者，并且在本轮选择（bases）里。"""
        record = await self._record(caller, source_id)
        try:
            position = Cursor.decode(cursor) if cursor else None
        except ValueError as error:
            raise InvalidInput(str(error)) from error
        source = self._build(record)
        kb = source.base_of(doc_ref)
        configured = self._open_range(record)
        if not admit(source_id, (kb,), configured, self._grants(caller))[0]:
            raise NotFound("文档不存在或你没有使用权限")
        if bases is not None and not Selection.of(bases).covers(source_id, kb):
            raise ScopeDenied("这份文档所在的知识库不在本轮选择的知识范围内")
        budget = max(1, min(max_chars, MAX_READ_CHARS))
        options = options or ReadOptions()
        try:
            request = ReadRequest(doc_ref=doc_ref, cursor=position, max_chars=budget, section=options.section,
                                  chunk=options.chunk, context=options.context, find=options.find,
                                  outline=options.outline)
        except ValidationError as error:
            raise InvalidInput("阅读参数不合法：section、chunk、find 只能选一个，outline 只能配 section，"
                               "context 为 0 到 5") from error
        missing = _unsupported(source, request.wants())
        if not missing:
            return await source.read(caller, request)
        result = await source.read(caller, request.model_copy(update={k: _READ_DEFAULTS[k] for k in missing}))
        # 只是不支持看目录、还能按章节读时，读的是那一节，不是从头顺序读
        kept = request.wants() - missing
        note = _ignored_note(record.name, missing) + (
            "" if kept & {"section", "chunk", "find"} else f"，已从{'游标处' if position else '开头'}顺序阅读")
        return result.model_copy(update={"notes": (*result.notes, note)})

    async def documents(self, caller: Caller, base: str, page: Page, *,
                        bases: tuple[str, ...] | None = None) -> tuple[DocumentPage, tuple[str, ...]]:
        """一个知识库里的文档（智能体的 knowledge_documents，照 WeKnora 的 list_documents）：知识库要在开放范围内、授权给了
        调用者、在本轮选择（bases）里。按名字找要知识源声明 document_query，不支持就列全部并说明。"""
        source_id, kb = parse_selector(base)
        if kb == ALL:
            raise InvalidInput("列文档要指定一个知识库，填知识库目录里的 id，不能用 <知识源>.*")
        record = await self._record(caller, source_id)
        source = self._build(record)
        if not admit(source_id, (kb,), self._open_range(record), self._grants(caller))[0]:
            raise NotFound("知识库不存在或你没有使用权限")
        if bases is not None and not Selection.of(bases).covers(source_id, kb):
            raise ScopeDenied(f"知识库 {base} 不在本轮选择的知识范围内")
        notes: tuple[str, ...] = ()
        if page.query and "document_query" not in getattr(source, "features", frozenset()):
            page = page.model_copy(update={"query": None})
            notes = (f"知识源「{record.name}」不支持按名字找文档，已按顺序列出",)
        return await source.list_documents(caller, Scope(dataset_ids=(kb,)), page), notes

    async def list_documents(self, caller: Caller, source_id: str, page: Page) -> DocumentPage:
        record = await self._record(caller, source_id)
        source = self._build(record)
        datasets = await self._datasets(caller, record, source, self._grants(caller), None)
        return await source.list_documents(caller, Scope(dataset_ids=datasets), page)

    def _grants(self, caller: Caller) -> Grants:
        return Grants(self._access.visible_resources(caller.user_id, caller.workspace_id, BASE_RESOURCE, "use"))

    async def _record(self, caller: Caller, source_id: str) -> SourceRecord:
        if not self._grants(caller).any_in(source_id):
            raise NotFound()
        provided = self._provided.get(source_id)
        record = provided.record(caller.workspace_id) if provided else await self._store.get(caller.workspace_id,
                                                                                               source_id)
        if not record.enabled:
            raise AccessDenied(f"知识源「{record.name}」已停用")
        return record

    async def _usable(self, caller: Caller, grants: Grants,
                      selection: Selection | None) -> tuple[list[SourceRecord], list["_Failure"]]:
        """要查的知识源。点名了的逐个说明为什么不能查；没点名时只看授权了知识库的来源，其余不提。"""
        if selection is not None and not selection.by_source:
            raise NoReadableKnowledge("本轮没有选择知识库")
        records = await self._records(caller.workspace_id)
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
        configured = self._open_range(record)
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
                          query: str, top_n: int, options: SearchOptions,
                          pins: tuple[Pinned, ...]) -> "tuple[list[Evidence] | _Failure, tuple[str, ...]]":
        """返回（结果或失败，给模型的说明：被忽略的条件、知识源自己交回的说明）。"""
        picked = selection.by_source[record.id] if selection else None
        try:
            source = self._build(record)
            datasets = await self._datasets(caller, record, source, grants, picked)
            scope, pin_notes = _pinned_scope(record, source, datasets, pins)
            if scope is None:
                return [], pin_notes
            request = SearchRequest(query=query, scope=scope, top_n=top_n, mode=options.mode,
                                    folders=options.folders, tags=options.tags, filters=options.filters)
            missing = _unsupported(source, request.wants())
            if missing:
                request = request.model_copy(update={key: _SEARCH_DEFAULTS[key] for key in missing})
            ignored = (_ignored_note(record.name, missing),) if missing else ()
            found = await source.search(caller, request)
            if isinstance(found, SearchHits):
                return list(found.evidence), (*pin_notes, *ignored, *found.notes)
            return found, (*pin_notes, *ignored)
        except ValidationError as error:
            raise InvalidInput("检索条件不合法：文件夹、标签最多各 20 个，元数据条件最多 10 个") from error
        except KnowledgeError as error:
            return _Failure(f"知识源「{record.name}」：{error}", transient=isinstance(error, SourceUnavailable)), ()
        except Exception:  # 连接器内部错误只记日志
            log.exception("knowledge source %s search failed", record.id)
            return _Failure(f"知识源「{record.name}」：暂时不可用", transient=True), ()


def _pinned_scope(record: SourceRecord, source: KnowledgeSource, datasets: tuple[str, ...],
                  pins: tuple[Pinned, ...]) -> tuple[Scope | None, tuple[str, ...]]:
    """这个知识源要查的范围：点名了文件、标签的库只查它们。不支持点名的知识源，要查的库都只点名了文档时
    合起来按文档查；做不到的那几个库这次不查、写明原因（不能当成整库查，那就放宽了用户的限定）。
    返回 None 表示这个知识源这次没有可查的。"""
    prefix = f"{record.id}."
    mine = tuple(Pin(dataset_id=p.base[len(prefix):], document_ids=p.documents, tags=p.tags)
                 for p in pins if p.base.startswith(prefix) and p.base[len(prefix):] in datasets)
    if not mine:
        return Scope(dataset_ids=datasets), ()
    if "pins" in source.features:
        return Scope(dataset_ids=datasets, pins=mine), ()
    pinned = {p.dataset_id for p in mine}
    if pinned == set(datasets) and not any(p.tags for p in mine):
        return Scope(dataset_ids=datasets, document_ids=tuple(d for p in mine for d in p.document_ids)), ()
    rest = tuple(d for d in datasets if d not in pinned)
    note = f"知识源「{record.name}」不支持只查 @ 点名的文件或标签，点名的那几个知识库这次没有查"
    return (Scope(dataset_ids=rest) if rest else None), (note,)


@dataclass(frozen=True)
class _Failure:
    text: str
    transient: bool = False  # 知识源暂时不可用（而不是调用者没有权限或身份）


def _nothing_readable(failures: list[_Failure]) -> KnowledgeError:
    detail = "；".join(f.text for f in failures)
    if failures and all(f.transient for f in failures):
        return SourceUnavailable(f"知识源暂时不可用：{detail}")
    return NoReadableKnowledge(f"本轮绑定的知识你一个都读不了：{detail}")
