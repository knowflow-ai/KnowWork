"""两个内置工具：knowledge_search、knowledge_read（技术方案 6.4 节）。

- 名称用下划线：宿主工具桥要求 ^[A-Za-z][A-Za-z0-9_-]{0,63}$，不允许点号；
- 参数 Schema 只用 DSH 官方 dsh-tools 支持的子集（type/properties/required/additionalProperties/items/enum/const
  与 description 等注解），没有 minLength、maximum 之类，长度与数量上限在服务端截断或拒绝；
- 结果里的证据都注册进本轮引用目录，模型按返回的 ref 写 [n]；超出目录上限的证据 ref 为 null 并明确说明；
- 范围按知识库指定（全局 id <知识源id>.<知识库id>，取自检索结果的 knowledge_base），不再按知识源（技术方案 8.1 节）；
- 只调 KnowledgeService，不知道背后是哪种知识源；不在工具里再起 Agent 循环。
"""

import json
from collections.abc import Awaitable, Callable
from typing import Any

from ..tools.registry import BuiltinTool, ToolContext, ToolFailure, ToolSpec
from .bases import Selection, base_id
from .citation_store import CitationStore
from .errors import KnowledgeError
from .models import MAX_READ_CHARS, Evidence
from .service import KnowledgeService

SEARCH_NAME = "knowledge_search"
READ_NAME = "knowledge_read"
CatalogOf = Callable[[ToolContext], str]
# 本轮选择的知识库（全局 id 或 <知识源id>.*）；None = 本人能查的全部
BoundBases = Callable[[ToolContext], Awaitable[tuple[str, ...] | None]]

SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "检索词，用自然语言描述要找的内容，1 到 1000 字"},
        "top_n": {"type": "integer", "description": "返回条数，1 到 20，默认 8"},
        "knowledge_bases": {"type": "array", "items": {"type": "string"},
                            "description": "只查这些知识库，填检索结果里的 knowledge_base；不填则查本轮可查的全部"},
    },
    "required": ["query"],
    "additionalProperties": False,
}

READ_SCHEMA = {
    "type": "object",
    "properties": {
        "source_id": {"type": "string", "description": "知识源，取自 knowledge_search 结果的 source_id"},
        "doc_ref": {"type": "string", "description": "文档引用，取自 knowledge_search 结果的 doc_ref"},
        "cursor": {"type": "string", "description": "续读游标，取自上一次 knowledge_read 的 next_cursor"},
        "max_chars": {"type": "integer", "description": f"本次最多读多少字，上限 {MAX_READ_CHARS}"},
    },
    "required": ["source_id", "doc_ref"],
    "additionalProperties": False,
}


# 引用规则：由宿主经官方 systemPrompt.section 写进系统提示（工具对该会话可见时才出现），描述里只留一句提示。
CITATION_GUIDANCE = (
    "引用规则：回答里用到知识检索或阅读的内容时，在对应句子末尾写上该证据的编号，格式为 [1]、[2]，"
    "编号只能取自 knowledge_search / knowledge_read 本会话返回过的 ref；多个出处连写 [1][3]。"
    "ref 为 null 的结果不能引用。不要编造编号，也不要写成 [ID:1]、[^1] 或带链接的形式；"
    "知识里找不到依据时直接说明，不要凭常识补全后再标编号。")


def _session_catalog(ctx: ToolContext) -> str:
    return ctx.session_id


async def _no_binding(_ctx: ToolContext) -> tuple[str, ...] | None:
    return None


def knowledge_tools(service: KnowledgeService, citations: CitationStore, *,
                    catalog_of: CatalogOf = _session_catalog,
                    bound_bases: BoundBases = _no_binding) -> tuple[BuiltinTool, BuiltinTool]:
    """catalog_of 决定一轮的引用目录 ID（默认按会话）；bound_bases 给出本轮选择的知识库（None = 本人能查的全部）。"""

    async def search(ctx: ToolContext, arguments: dict[str, Any]) -> str:
        caller = await service.caller(ctx.workspace_id, ctx.user_id)
        requested = _within(tuple(str(x) for x in arguments.get("knowledge_bases") or ()), await bound_bases(ctx))
        outcome = await _guard(service.search(caller, str(arguments["query"]),
                                              top_n=_int(arguments.get("top_n"), 8), bases=requested))
        numbers, catalog = await citations.register(ctx.workspace_id, catalog_of(ctx), outcome.evidence)
        payload = {"results": [_hit(e, n) for e, n in zip(outcome.evidence, numbers, strict=True)],
                   "notes": list(outcome.notes)}
        if any(n is None for n in numbers):
            payload["notes"].append(f"本轮引用已达上限 {catalog.limit} 条，ref 为 null 的结果不能被引用")
        if not outcome.evidence:
            payload["notes"].append("在可读的知识范围内没有找到相关内容")
        return json.dumps(payload, ensure_ascii=False)

    async def read(ctx: ToolContext, arguments: dict[str, Any]) -> str:
        caller = await service.caller(ctx.workspace_id, ctx.user_id)
        result = await _guard(service.read(caller, str(arguments["source_id"]), str(arguments["doc_ref"]),
                                           cursor=arguments.get("cursor") or None,
                                           max_chars=_int(arguments.get("max_chars"), MAX_READ_CHARS),
                                           bases=await bound_bases(ctx)))
        numbers, _ = await citations.register(ctx.workspace_id, catalog_of(ctx), result.spans)
        return json.dumps({"title": result.title, "doc_ref": result.doc_ref,
                           "passages": [{"ref": n, "text": s.text} for s, n in zip(result.spans, numbers, strict=True)],
                           "next_cursor": result.next_cursor.encode() if result.next_cursor else None},
                          ensure_ascii=False)

    return (
        BuiltinTool(ToolSpec(SEARCH_NAME, "在本轮可查的知识库里检索，返回带引用编号 ref 的片段。回答引用时写 [ref]。",
                             SEARCH_SCHEMA, "read", guidance=CITATION_GUIDANCE), search),
        BuiltinTool(ToolSpec(READ_NAME, f"按文档引用连续阅读原文，单次最多 {MAX_READ_CHARS} 字，用 next_cursor 续读。"
                                        "回答引用时写段落的 [ref]。", READ_SCHEMA, "read", guidance=CITATION_GUIDANCE),
                    read),
    )


def _within(asked: tuple[str, ...], bound: tuple[str, ...] | None) -> tuple[str, ...] | None:
    """模型点名的知识库必须在本轮选择的范围内；没点名就用本轮选择。"""
    if asked and bound is not None:
        try:
            outside = Selection.of(asked).outside(Selection.of(bound))
        except KnowledgeError as error:
            raise ToolFailure(str(error)) from error
        if outside:
            raise ToolFailure(f"知识库 {outside} 不在本轮选择的知识范围内")
    return asked or bound


async def _guard(awaitable):
    try:
        return await awaitable
    except KnowledgeError as error:
        raise ToolFailure(str(error)) from error


def _int(value: Any, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _hit(evidence: Evidence, number: int | None) -> dict[str, Any]:
    locator = evidence.locator
    return {"ref": number, "source_id": evidence.source_id,
            "knowledge_base": base_id(evidence.source_id, evidence.kb_id) if evidence.kb_id else None,
            "doc_ref": evidence.doc_ref, "title": evidence.title, "page": locator.page if locator else None,
            "text": evidence.text}
