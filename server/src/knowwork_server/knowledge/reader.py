"""对外检索：REST 接口与 MCP 共用的三个操作——列出知识库、检索、阅读。

- 身份由调用方（路由或 MCP 外层）按 API Key 现场判定后传进来，这里不缓存身份；
- 范围收窄、各知识源支持哪些参数，和 Agent 的 knowledge_search / knowledge_read 一样（同一个 KnowledgeService）；
- 返回给程序看的完整证据：定位、原文链接、版本提示，图片给至少 2 小时有效的签名链接（不暴露对象存储）；
- 不登记引用目录（没有会话），阅读时按检索结果里的 span_ref 从指定片段读。
"""

from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from .links import LinkSigner
from .models import MAX_READ_CHARS, Caller, Evidence, MetaFilter, SearchMode
from .service import MAX_TOP_N, KnowledgeService, ReadOptions, SearchOptions


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchBody(_In):
    query: Annotated[str, Field(min_length=1, max_length=1000)]
    top_n: Annotated[int, Field(ge=1, le=MAX_TOP_N)] = 8
    knowledge_bases: Annotated[list[str] | None, Field(max_length=50)] = None
    mode: SearchMode = "hybrid"
    folders: Annotated[list[str], Field(max_length=20)] = []
    tags: Annotated[list[str], Field(max_length=20)] = []
    filters: Annotated[list[MetaFilter], Field(max_length=10)] = []
    # 这一次开不开多跳、视觉检索；不填按知识库自己的默认（知识库设成关闭的打不开）
    multi_hop: bool | None = None
    visual: bool | None = None


class ReadBody(_In):
    source_id: Annotated[str, Field(min_length=1, max_length=64)]
    doc_ref: Annotated[str, Field(min_length=1, max_length=500)]
    cursor: str | None = None
    max_chars: Annotated[int, Field(ge=100, le=MAX_READ_CHARS)] = MAX_READ_CHARS
    section: Annotated[str | None, Field(max_length=200)] = None
    chunk: Annotated[str | None, Field(max_length=200)] = None  # 检索结果里的 span_ref
    context: Annotated[int, Field(ge=0, le=5)] = 1
    find: Annotated[str | None, Field(max_length=200)] = None
    outline: bool = False  # 看目录；配 section 只看那一章下面的


@dataclass(frozen=True)
class KnowledgeReader:
    service: KnowledgeService
    links: LinkSigner

    async def bases(self, caller: Caller, knowledge_bases: list[str] | None = None) -> dict[str, Any]:
        listing = await self.service.bases(caller, bases=tuple(knowledge_bases) if knowledge_bases else None)
        return {"knowledge_bases": [_base(b) for b in listing.bases], "notes": list(listing.notes)}

    async def search(self, caller: Caller, body: SearchBody) -> dict[str, Any]:
        options = SearchOptions(mode=body.mode, folders=tuple(body.folders), tags=tuple(body.tags),
                                filters=tuple(body.filters), multi_hop=body.multi_hop, visual=body.visual)
        outcome = await self.service.search(caller, body.query, top_n=body.top_n,
                                            bases=tuple(body.knowledge_bases) if body.knowledge_bases else None,
                                            options=options)
        return {"results": [self._evidence(caller, e) for e in outcome.evidence], "notes": list(outcome.notes)}

    async def read(self, caller: Caller, body: ReadBody) -> dict[str, Any]:
        options = ReadOptions(section=body.section, chunk=body.chunk, context=body.context, find=body.find,
                              outline=body.outline)
        result = await self.service.read(caller, body.source_id, body.doc_ref, cursor=body.cursor,
                                         max_chars=body.max_chars, options=options)
        return {"doc_ref": result.doc_ref, "title": result.title, "text": result.text,
                "passages": [self._evidence(caller, e) for e in result.spans],
                "next_cursor": result.next_cursor.encode() if result.next_cursor else None,
                "notes": list(result.notes),
                **({"outline": [node.model_dump(mode="json") for node in result.outline]} if result.outline else {})}

    def _evidence(self, caller: Caller, evidence: Evidence) -> dict[str, Any]:
        data: dict[str, Any] = {
            "source_id": evidence.source_id,
            "knowledge_base": f"{evidence.source_id}.{evidence.kb_id}" if evidence.kb_id else evidence.source_id,
            "doc_ref": evidence.doc_ref, "span_ref": evidence.span_ref, "title": evidence.title,
            "text": evidence.text, "score": evidence.score,
        }
        if evidence.locator is not None:
            data["locator"] = evidence.locator.model_dump(mode="json", exclude_defaults=True)
        if evidence.open_url:
            data["open_url"] = evidence.open_url
        if evidence.images:
            data["images"] = [{"ref": image.ref, "caption": image.caption,
                               "url": self.links.sign(image.url, caller.user_id) if image.url else None}
                              for image in evidence.images]
        if evidence.version_note:
            data["version_note"] = evidence.version_note
        return data


def _base(listed) -> dict[str, Any]:
    info = listed.info
    base: dict[str, Any] = {"id": listed.id, "name": info.name, "source": listed.source_name,
                            "description": info.description, "filterable": listed.filterable}
    if listed.filterable:
        base["fields"] = [field.model_dump() for field in info.fields]
    if info.routes:
        base["routes"] = list(info.routes)  # 能按次打开的检索方式：multi_hop、visual
    return base
