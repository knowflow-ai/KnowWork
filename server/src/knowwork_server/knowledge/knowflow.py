"""KnowFlow 连接器：调 RAGFlow SDK 接口（技术方案 5.4、8.4 节）。

两种身份模式：
- shared：统一账号，所有人都用管理员配置的同一个 KnowFlow API Key（Authorization: Bearer）。KnowFlow 只看到
  这一个账号，权限只靠 KnowWork 的知识库授权（第 5.4 节第 1 层）。开放范围留空就是这个账号读得到的全部；
- assertion：每个请求带一个新签的 60 秒断言（头 X-KnowFlow-Assertion），KnowFlow 按邮箱找到用户、按本人 RBAC 过滤。
  要 KnowFlow 先支持，界面上暂时隐藏。

要查哪些知识库由业务服务算好再交进来（Scope.dataset_ids，已与授权取过交集）；连接器只再挡一次开放范围以外的。

KnowFlow 的 API Key 门（api/utils/api_key_gate.py）对一次请求里的知识库全有或全无：有一个读不了就整体 403，
并在 data.denied_resources 列出读不了的。连接器据此去掉它们重试一次，所以权限不同的用户拿到的证据范围不同。
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from .assertion import HEADER_NAME, AssertionSigner
from .errors import IdentityUnavailable, InvalidInput, KnowledgeError, ScopeDenied, SourceUnavailable
from .knowflow_mapping import KnowFlowConfig, document_of, evidence_of, split_doc_ref
from .models import BaseInfo, Caller, Cursor, DocumentPage, Evidence, Page, ReadResult, Scope
from .rrf import rrf_merge

log = logging.getLogger(__name__)

API = "/api/v1"
AUTH_ERROR_CODE = 109  # RAGFlow RetCode.AUTHENTICATION_ERROR：API Key 无效
MIXED_MODELS_MARK = "different embedding models"  # RAGFlow 检索接口：一次请求里的知识库嵌入模型不一致
CHUNK_PAGE_SIZE = 64
MAX_CHUNK_PAGES_PER_READ = 4
DATASET_PAGE_SIZE = 100
MAX_DATASET_PAGES = 10


class IdentityProvider(Protocol):
    async def headers(self, caller: Caller) -> dict[str, str]: ...

    def rejected(self, source_name: str) -> str:
        """KnowFlow 说 API Key 无效时给用户看的话。"""
        ...


@dataclass(frozen=True)
class AssertionIdentity:
    signer: AssertionSigner

    async def headers(self, caller: Caller) -> dict[str, str]:
        return {HEADER_NAME: self.signer.issue(caller)}

    def rejected(self, source_name: str) -> str:
        return (f"知识源「{source_name}」没有接受签名断言：这个 KnowFlow 还不支持签名断言，或服务密钥不对。"
                "请联系管理员改用统一账号")


@dataclass(frozen=True)
class SharedAccountIdentity:
    key: str

    async def headers(self, caller: Caller) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.key}"}

    def rejected(self, source_name: str) -> str:
        return f"知识源「{source_name}」配置的统一账号 Key 无效或已失效，请联系管理员"


class _MixedModels(Exception):
    """一次检索里的知识库用了不同的嵌入模型，KnowFlow 拒绝（向量不能混在一起比）。"""


class _Denied(Exception):
    def __init__(self, resources: tuple[str, ...]) -> None:
        super().__init__("denied")
        self.resources = resources


_ASSERTION_IDENTITY_ERRORS = {
    "assertion_user_not_found": "你在 KnowFlow 没有对应账号（按你的邮箱未找到），不能使用这个知识源",
    "assertion_user_disabled": "你的 KnowFlow 账号已停用，不能使用这个知识源",
}


class KnowFlowSource:
    def __init__(self, *, source_id: str, name: str, base_url: str, config: KnowFlowConfig,
                 identity: IdentityProvider, client: httpx.AsyncClient) -> None:
        self.source_id = source_id
        self._name = name
        self._base = base_url.rstrip("/")
        self._web = config.web_base_url or self._base
        self._config = config
        self._identity = identity
        self._client = client

    # ---- 知识源接口 ----

    async def list_bases(self, caller: Caller) -> tuple[BaseInfo, ...]:
        configured = self._config.dataset_ids
        rows = await self._dataset_rows(caller)
        return tuple(BaseInfo(id=r["id"], name=str(r.get("name") or r["id"])) for r in rows
                     if not configured or r["id"] in configured)

    def base_of(self, doc_ref: str) -> str:
        return self._doc(doc_ref)[0]

    async def search(self, caller: Caller, query: str, scope: Scope, top_n: int) -> list[Evidence]:
        """先合并查一次；KnowFlow 说嵌入模型不一致时，按模型分组各查一次，再按名次融合（各组分数口径不同）。"""
        datasets = self._in_range(scope)
        body = {"question": query, "page": 1, "page_size": top_n, "document_ids": list(scope.document_ids),
                "similarity_threshold": self._config.similarity_threshold,
                "vector_similarity_weight": self._config.vector_similarity_weight, "highlight": False}
        try:
            return _distinct(await self._search_group(caller, datasets, body, top_n))
        except _MixedModels:
            groups = await self._model_groups(caller, datasets)
        results = await asyncio.gather(*(self._search_group(caller, g, body, top_n) for g in groups),
                                       return_exceptions=True)
        found = [r for r in results if isinstance(r, list)]
        if not found:
            raise next(r for r in results if isinstance(r, BaseException))
        return _distinct(rrf_merge(found, top_n=top_n))

    async def _search_group(self, caller: Caller, datasets: tuple[str, ...], body: dict,
                            top_n: int) -> list[Evidence]:
        data = await self._with_readable(caller, datasets, lambda ids: self._call(
            caller, "POST", f"{API}/retrieval", json={**body, "dataset_ids": list(ids)}))
        chunks = data.get("chunks") if isinstance(data, dict) else None
        if not isinstance(chunks, list):
            raise self._unavailable("retrieval returned no chunk list")
        found = (evidence_of(self.source_id, self._web, c) for c in chunks if isinstance(c, dict))
        return [item for item in found if item is not None][:top_n]

    async def _model_groups(self, caller: Caller, datasets: tuple[str, ...]) -> tuple[tuple[str, ...], ...]:
        """按嵌入模型给知识库分组，保持原顺序；列表里查不到模型的（多半读不了）各自单独一组。"""
        models = {d["id"]: str(d.get("embedding_model") or "") for d in await self._dataset_rows(caller)}
        groups: dict[str, list[str]] = {}
        for dataset_id in datasets:
            key = models.get(dataset_id) or f"unknown:{dataset_id}"
            groups.setdefault(key, []).append(dataset_id)
        return tuple(tuple(ids) for ids in groups.values())

    async def read(self, caller: Caller, doc_ref: str, cursor: Cursor | None, max_chars: int) -> ReadResult:
        dataset_id, document_id = self._doc(doc_ref)
        position = cursor or Cursor()
        spans: list[Evidence] = []
        text, title, used = "", document_id, 0
        while len(text) < max_chars and used < MAX_CHUNK_PAGES_PER_READ:
            chunks, title, total, parsed = await self._chunk_page(caller, dataset_id, document_id, position.page)
            used += 1
            if total == 0 and parsed > 0:
                raise self._unlistable(title)
            text, position, spans = _consume(self.source_id, self._web, chunks, position, text, max_chars, spans)
            if position.index < len(chunks):
                break  # 本页没读完就是字数到了
            if position.page * CHUNK_PAGE_SIZE >= total or not chunks:
                return ReadResult(doc_ref=doc_ref, title=title, text=text, spans=tuple(spans), next_cursor=None)
            position = Cursor(page=position.page + 1)
        return ReadResult(doc_ref=doc_ref, title=title, text=text, spans=tuple(spans), next_cursor=position)

    async def list_documents(self, caller: Caller, scope: Scope, page: Page) -> DocumentPage:
        datasets = self._in_range(scope)
        index, number = _page_token(page.token)
        if index >= len(datasets):
            return DocumentPage(items=())
        dataset_id = datasets[index]
        data = await self._scoped(lambda: self._call(caller, "GET", f"{API}/datasets/{dataset_id}/documents",
                                                     params={"page": number, "page_size": page.size}))
        docs = data.get("docs") if isinstance(data, dict) else None
        if not isinstance(docs, list):
            raise self._unavailable("document list malformed")
        items = tuple(d for d in (document_of(self._web, dataset_id, x) for x in docs if isinstance(x, dict)) if d)
        total = data.get("total") if isinstance(data.get("total"), int) else 0
        more = number * page.size < total
        token = f"{index}.{number + 1}" if more else (f"{index + 1}.1" if index + 1 < len(datasets) else None)
        return DocumentPage(items=items, next_token=token)

    async def probe(self, caller: Caller) -> None:
        """签名断言：以操作者身份发一个最小请求。统一账号：开放范围里的知识库这个账号都要读得到，
        范围留空时至少要读得到一个（第 8.4 节）。"""
        if self._config.auth_mode != "shared":
            await self._call(caller, "GET", f"{API}/datasets", params={"page": 1, "page_size": 1})
            return
        readable = {base.id for base in await self.list_bases(caller)}
        missing = [d for d in self._config.dataset_ids if d not in readable]
        if missing:
            raise ScopeDenied(f"统一账号读不到开放范围里的知识库：{'、'.join(missing[:5])}")
        if not readable:
            raise ScopeDenied("统一账号在 KnowFlow 里一个知识库都读不到")

    # ---- 范围 ----

    def _in_range(self, scope: Scope) -> tuple[str, ...]:
        configured = self._config.dataset_ids
        outside = [d for d in scope.dataset_ids if configured and d not in configured]
        if outside:
            raise InvalidInput(f"知识库 {outside[0]} 不在知识源「{self._name}」的开放范围内")
        return scope.dataset_ids

    async def _dataset_rows(self, caller: Caller) -> list[dict]:
        """调用者在 KnowFlow 读得到的知识库（原始行，至少带 id），按页取完，最多 MAX_DATASET_PAGES 页。"""
        rows: list[dict] = []
        for page in range(1, MAX_DATASET_PAGES + 1):
            data = await self._call(caller, "GET", f"{API}/datasets",
                                    params={"page": page, "page_size": DATASET_PAGE_SIZE})
            batch = [d for d in data if isinstance(d, dict) and isinstance(d.get("id"), str)] \
                if isinstance(data, list) else []
            rows.extend(batch)
            if not isinstance(data, list) or len(data) < DATASET_PAGE_SIZE:
                break
        return rows

    async def _with_readable(self, caller: Caller, datasets: tuple[str, ...], run) -> Any:
        try:
            return await run(datasets)
        except _Denied as denied:
            remaining = tuple(d for d in datasets if d not in denied.resources)
        if not remaining:
            raise ScopeDenied(f"你对知识源「{self._name}」绑定的知识库都没有读取权限")
        try:
            return await run(remaining)
        except _Denied as error:
            raise ScopeDenied(f"你对知识源「{self._name}」绑定的知识库都没有读取权限") from error

    async def _scoped(self, run) -> Any:
        try:
            return await run()
        except _Denied as error:
            raise ScopeDenied(f"你没有读取知识源「{self._name}」中该内容的权限") from error

    def _doc(self, doc_ref: str) -> tuple[str, str]:
        try:
            dataset_id, document_id = split_doc_ref(doc_ref)
        except ValueError as error:
            raise InvalidInput("文档引用格式错误") from error
        if self._config.dataset_ids and dataset_id not in self._config.dataset_ids:
            raise InvalidInput(f"该文档不在知识源「{self._name}」的开放范围内")
        return dataset_id, document_id

    async def _chunk_page(self, caller: Caller, dataset_id: str, document_id: str,
                          page: int) -> tuple[list[dict], str, int, int]:
        """返回本页片段、文档名、可列出的片段总数，以及文档信息里记的分块数（两者对不上就是列不出来）。"""
        path = f"{API}/datasets/{dataset_id}/documents/{document_id}/chunks"
        data = await self._scoped(lambda: self._call(caller, "GET", path,
                                                     params={"page": page, "page_size": CHUNK_PAGE_SIZE}))
        chunks = data.get("chunks") if isinstance(data, dict) else None
        if not isinstance(chunks, list):
            raise self._unavailable("chunk list malformed")
        doc = data.get("doc") if isinstance(data.get("doc"), dict) else {}
        count = doc.get("chunk_count")
        total = data.get("total") if isinstance(data.get("total"), int) else len(chunks)
        return ([c for c in chunks if isinstance(c, dict)], str(doc.get("name") or document_id), total,
                count if isinstance(count, int) else 0)

    # ---- HTTP ----

    async def _call(self, caller: Caller, method: str, path: str, **kwargs: Any) -> Any:
        headers = await self._identity.headers(caller)
        try:
            response = await self._client.request(method, f"{self._base}{path}", headers=headers,
                                                  timeout=self._config.timeout_seconds, **kwargs)
        except httpx.HTTPError as error:
            raise self._unavailable(f"transport error: {type(error).__name__}") from error
        return self._unwrap(response)

    def _unwrap(self, response: httpx.Response) -> Any:
        body = _json(response)
        if response.status_code == 401:
            message = _ASSERTION_IDENTITY_ERRORS.get(str(body.get("error_code", "")))
            if message:
                raise IdentityUnavailable(message)
            raise self._unavailable(f"identity rejected: {body.get('error_code', 'unknown')}")
        if response.status_code == 403:
            denied = (body.get("data") or {}).get("denied_resources") if isinstance(body.get("data"), dict) else None
            raise _Denied(tuple(str(x) for x in denied) if isinstance(denied, list) else ())
        if response.status_code >= 400:
            raise self._unavailable(f"http {response.status_code}")
        code = body.get("code")
        if MIXED_MODELS_MARK in str(body.get("message", "")):
            raise _MixedModels()
        # KnowFlow 没收到它认得的凭据时回 code 0、data false（「`Authorization` can't be empty」），不能当成空结果：
        # 不支持签名断言的 KnowFlow 会忽略断言头，这样回，曾被读成「一个知识库都没有」（2026-10-20 实测）
        if code == AUTH_ERROR_CODE or (code == 0 and body.get("data") is False):
            raise IdentityUnavailable(self._identity.rejected(self._name))
        if code != 0:
            raise self._unavailable(f"code {code}: {str(body.get('message', ''))[:200]}")
        return body.get("data")

    def _unlistable(self, title: str) -> KnowledgeError:
        """文档有分块，KnowFlow 却按顺序列不出来（共享知识库按调用者索引查的已知问题）。"""
        log.warning("knowflow source %s cannot list chunks of %s", self.source_id, title)
        return SourceUnavailable(f"知识源「{self._name}」暂时无法按顺序读取《{title}》的正文，请改用 knowledge_search 检索需要的内容")

    def _unavailable(self, detail: str) -> KnowledgeError:
        log.warning("knowflow source %s unavailable: %s", self.source_id, detail)
        return SourceUnavailable(f"知识源「{self._name}」暂时不可用")


def _distinct(evidence: list[Evidence]) -> list[Evidence]:
    """同一份文档传进多个知识库时会命中内容相同的片段：按标题加正文只留第一条，不浪费引用编号。"""
    seen: set[tuple[str, str]] = set()
    kept: list[Evidence] = []
    for item in evidence:
        key = (item.title, " ".join(item.text.split()))
        if key not in seen:
            seen.add(key)
            kept.append(item)
    return kept


def _json(response: httpx.Response) -> dict:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _page_token(token: str | None) -> tuple[int, int]:
    if not token:
        return 0, 1
    try:
        index, number = (int(x) for x in token.split("."))
    except ValueError as error:
        raise InvalidInput("分页标记格式错误") from error
    if index < 0 or number < 1:
        raise InvalidInput("分页标记格式错误")
    return index, number


def _consume(source_id: str, web: str, chunks: list[dict], position: Cursor, text: str, max_chars: int,
             spans: list[Evidence]) -> tuple[str, Cursor, list[Evidence]]:
    """从 position 起把本页片段拼进正文，直到字数用完。返回新正文、新位置、新片段列表（不改入参）。"""
    index, offset, out = position.index, position.offset, list(spans)
    while index < len(chunks) and len(text) < max_chars:
        evidence = evidence_of(source_id, web, chunks[index])
        content = evidence.text if evidence else ""
        take = content[offset:offset + (max_chars - len(text))]
        text += take
        if evidence and take:
            out.append(evidence.model_copy(update={"text": take}))
        if offset + len(take) < len(content):
            return text, Cursor(page=position.page, index=index, offset=offset + len(take)), out
        index, offset = index + 1, 0
        if index < len(chunks) and len(text) + 2 < max_chars:
            text += "\n\n"
    return text, Cursor(page=position.page, index=index, offset=0), out
