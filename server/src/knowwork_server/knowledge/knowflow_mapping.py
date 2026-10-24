"""KnowFlow（RAGFlow 二开）的配置模型与返回字段映射。

RAGFlow SDK `POST /api/v1/retrieval` 返回 `{"code": 0, "data": {"chunks": [...], "doc_aggs": ..., "total": n}}`，
每个 chunk 的字段（api/apps/sdk/doc.py 的 key_mapping 之后）：
id、content、document_id、document_keyword（文档名）、dataset_id、positions、similarity、image_id、doc_type_kwd。
positions 是 [[页码(从 1 起), x0, x1, top, bottom], ...]（rag/svr/task_executor.py 的 add_positions），
坐标是解析时的版面坐标，接口里没有页面宽高，换算不成占页面的比例，所以定位只取页码，不填框；
在 KnowFlow 里看原文时由它自己画框（open_url 带页码跳过去）。
"""

import re
from typing import Any, Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import DocumentInfo, Evidence, Locator, clip_quote

_ID = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
# 认证方式：统一账号（KnowFlow 始终看到同一个账号，谁能查只看 KnowWork 的知识库授权）；
# 签名断言（KnowWork 代表本人访问）要 KnowFlow 先支持，界面上暂时隐藏。个人 Key 方式 2026-10-20 去掉
AuthMode = Literal["shared", "assertion"]


class KnowFlowConfig(BaseModel):
    """知识源的非敏感配置（存 knowledge_source.config）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    auth_mode: AuthMode = "shared"
    # 开放范围：开放给 KnowWork 的知识库。空 = 在 KnowFlow 读得到的全部（统一账号方式下是那个账号读得到的全部，
    # 含它以后新建的；签名断言方式下是调用者本人读得到的）；谁能查哪个另由知识库授权决定
    dataset_ids: tuple[str, ...] = Field(default=(), max_length=50)
    web_base_url: str | None = Field(default=None, max_length=500)    # 打开原文的前端地址，空则用 base_url
    similarity_threshold: float = Field(default=0.2, ge=0, le=1)
    vector_similarity_weight: float = Field(default=0.3, ge=0, le=1)
    timeout_seconds: float = Field(default=15, ge=1, le=60)

    @field_validator("dataset_ids")
    @classmethod
    def _ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not _ID.fullmatch(item) for item in value):
            raise ValueError("知识库 ID 格式不合法")
        return tuple(dict.fromkeys(value))

    @field_validator("web_base_url")
    @classmethod
    def _url(cls, value: str | None) -> str | None:
        return validate_base_url(value) if value else None



def validate_base_url(value: str) -> str:
    cleaned = (value or "").strip().rstrip("/")
    if not re.fullmatch(r"https?://[^\s/?#]+(/[^\s?#]*)?", cleaned):
        raise ValueError("地址必须是 http(s):// 开头的完整地址，且不带查询参数")
    return cleaned


def split_doc_ref(doc_ref: str) -> tuple[str, str]:
    dataset_id, sep, document_id = (doc_ref or "").partition("/")
    if not sep or not _ID.fullmatch(dataset_id) or not _ID.fullmatch(document_id):
        raise ValueError("文档引用格式错误")
    return dataset_id, document_id


def doc_ref_of(dataset_id: str, document_id: str) -> str:
    return f"{dataset_id}/{document_id}"


def open_url(web_base: str, document_id: str, title: str, page: int | None) -> str:
    """KnowFlow 前端的原文查看页（web/src/routes.ts 的 /document/:id，查询参数 ext、prefix）。"""
    ext = title.rsplit(".", 1)[-1].lower() if "." in title else ""
    url = f"{web_base}/document/{quote(document_id)}?ext={quote(ext)}&prefix=document"
    return f"{url}&page={page}" if page else url


def locator_of(positions: Any, content: str = "") -> Locator | None:
    """取第一个合法位置的页码；没有合法位置时返回 None（例如 Word 解析出的片段不带位置）。"""
    for item in positions if isinstance(positions, list) else []:
        if isinstance(item, (list, tuple)) and len(item) >= 5:
            try:
                page = int(item[0])
            except (TypeError, ValueError):
                continue
            if page >= 1:
                return Locator(type="pdf", page=page, quote=clip_quote(content))
    return None


def evidence_of(source_id: str, web_base: str, chunk: dict[str, Any]) -> Evidence | None:
    """把一个 RAGFlow chunk 映射成证据；缺少关键字段的丢弃（返回 None）。"""
    chunk_id, document_id = chunk.get("id"), chunk.get("document_id")
    dataset_id, content = chunk.get("dataset_id"), chunk.get("content")
    if not all(isinstance(v, str) and v for v in (chunk_id, document_id, dataset_id)) or not isinstance(content, str):
        return None
    title = str(chunk.get("document_keyword") or chunk.get("docnm_kwd") or document_id)
    locator = locator_of(chunk.get("positions"), content)
    score = chunk.get("similarity")
    return Evidence(source_id=source_id, kb_id=dataset_id, doc_ref=doc_ref_of(dataset_id, document_id), span_ref=chunk_id,
                    title=title, text=content, locator=locator,
                    open_url=open_url(web_base, document_id, title, locator.page if locator else None),
                    score=float(score) if isinstance(score, (int, float)) else None)


def document_of(web_base: str, dataset_id: str, doc: dict[str, Any]) -> DocumentInfo | None:
    document_id, name = doc.get("id"), doc.get("name")
    if not isinstance(document_id, str) or not document_id:
        return None
    title = str(name or document_id)
    return DocumentInfo(doc_ref=doc_ref_of(dataset_id, document_id), title=title,
                        open_url=open_url(web_base, document_id, title, None))
