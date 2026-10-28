"""两个内置工具：knowledge_search、knowledge_read。

- 名称用下划线：宿主工具桥要求 ^[A-Za-z][A-Za-z0-9_-]{0,63}$，不允许点号；
- 参数 Schema 只用 DSH 官方 dsh-tools 支持的子集（type/properties/required/additionalProperties/items/enum/const
  与 description 等注解），没有 minLength、maximum 之类，长度与数量上限在服务端截断或拒绝；
- 结果里的证据都注册进本轮引用目录，模型按返回的 ref 写 [n]；超出目录上限的证据 ref 为 null 并明确说明；
- 范围按知识库指定（全局 id <知识源id>.<知识库id>，取自检索结果的 knowledge_base），不再按知识源；
- 只调 KnowledgeService，不知道背后是哪种知识源；不在工具里再起 Agent 循环。
"""

import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any

from pydantic import ValidationError

from ..tools.registry import BuiltinTool, ToolContext, ToolFailure, ToolSpec
from .bases import Selection, base_id
from .citation_store import CitationStore
from .errors import KnowledgeError
from .fetch_tool import SessionInput, fetch_tool
from .models import MAX_CONTEXT, MAX_READ_CHARS, DocumentInfo, Evidence, EvidenceImage, MetaFilter, OutlineNode, Page
from .search_log import SearchLog
from .service import KnowledgeService, Pinned, ReadOptions, SearchOptions
from .turn_ledger import TurnLedger

SEARCH_NAME = "knowledge_search"
READ_NAME = "knowledge_read"
BASES_NAME = "knowledge_bases"
DOCUMENTS_NAME = "knowledge_documents"
MAX_LISTED_DOCUMENTS = 50
MAX_LISTED = 50
FILTER_RULES = ("filters 里几个条件同时满足；ne 不算没填这个字段的文档；multi 为 true 的字段能填多个值，eq、in 按「带其中任何一个」，"
                "contains 按等于；数字、日期也写成字符串，日期可以只写到年或月（2025、2025-03）；"
                "tags 是带其中任何一个标签，标签就是字段「标签」的可选值")
CatalogOf = Callable[[ToolContext], str]


@dataclass(frozen=True)
class TurnScope:
    """检索工具这一轮要遵守的范围，来自对话本轮绑定的运行配置。

    bases：选了的知识库（全局 id 或 <知识源id>.*），None = 本人能查的全部；dropped：用户在回答上方撤掉的检索条件
    （condition_label 的写法）；pins：用户 @ 点名的文件与标签，那几个库只查它们。"""

    bases: tuple[str, ...] | None = None
    dropped: frozenset[str] = frozenset()
    pins: tuple[Pinned, ...] = ()


TurnScopeOf = Callable[[ToolContext], Awaitable[TurnScope]]

BASES_SCHEMA = {
    "type": "object",
    "properties": {
        "knowledge_bases": {"type": "array", "items": {"type": "string"},
                            "description": "要看哪些知识库，填目录里的 id；不填则列出本轮可查的全部"},
    },
    "additionalProperties": False,
}

DOCUMENTS_SCHEMA = {
    "type": "object",
    "properties": {
        "knowledge_base": {"type": "string", "description": "哪个知识库，填知识库目录里的 id"},
        "keyword": {"type": "string", "description": "可选：只列标题或文件名里有这几个字的"},
        "cursor": {"type": "string", "description": "翻页游标，取自上一次的 next_cursor"},
        "limit": {"type": "integer", "description": f"这一页列几份，1 到 {MAX_LISTED_DOCUMENTS}，默认 20"},
    },
    "required": ["knowledge_base"],
    "additionalProperties": False,
}

SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        # 写法照 WeKnora 的 search_knowledge：语义检索按意思打分，堆关键词反而不准
        "query": {"type": "string", "description": "检索词，1 到 1000 字。hybrid、semantic 写成一句完整的问题，说清对象和想知道"
                                                   "什么，不要堆关键词；keyword 写要原样命中的词"},
        "top_n": {"type": "integer", "description": "返回条数，1 到 20，默认 8"},
        "knowledge_bases": {"type": "array", "items": {"type": "string"},
                            "description": "只查这些知识库，填检索结果里的 knowledge_base；不填则查本轮可查的全部"},
        "mode": {"type": "string", "enum": ["hybrid", "semantic", "keyword"],
                 "description": "检索方式，默认 hybrid（语义加关键词）；编号、报错码、型号、人名这类要原样命中的用 keyword"},
        "folders": {"type": "array", "items": {"type": "string"},
                    "description": "只查知识库里这些文件夹（相对路径，如「合同/2025」，含子文件夹）"},
        "tags": {"type": "array", "items": {"type": "string"},
                 "description": "只查带其中任何一个标签的文档；要几个标签都带，用 filters 写几个条件"},
        "filters": {
            "type": "array",
            "description": "按文档元数据筛选，几个条件同时满足；字段名用知识库里定义的元数据字段",
            "items": {
                "type": "object",
                "properties": {
                    "field": {"type": "string", "description": "元数据字段名"},
                    "op": {"type": "string", "enum": ["eq", "ne", "in", "gt", "gte", "lt", "lte", "contains"],
                           "description": "比较方式，默认 eq；in 时用 values 给候选值"},
                    "value": {"type": "string", "description": "比较的值，数字与日期也写成字符串"},
                    "values": {"type": "array", "items": {"type": "string"}, "description": "in 的候选值"},
                },
                "required": ["field"],
                "additionalProperties": False,
            },
        },
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
        "section": {"type": "string", "description": "只读这一节（含子节）：目录里的编号（如 2.1，先用 outline 看目录），"
                                                     "或章节标题、「一级/二级」这样的路径；没读完时带上同一个 section 和 "
                                                     "next_cursor 续读"},
        "outline": {"type": "boolean", "description": "看这份文档的目录（每节的编号、页码、字数、图表数）；配 section 只看那一章"
                                                     "下面的子目录。章节层级不可靠的文档没有目录"},
        "chunk": {"type": "integer", "description": "从这个引用编号（检索结果的 ref）的片段读起，看它的上下文"},
        "context": {"type": "integer", "description": f"配合 chunk，前后各带几个片段，0 到 {MAX_CONTEXT}，默认 1"},
        "find": {"type": "string", "description": "在这份文档里查找的词或正则，返回命中处附近的内容"},
    },
    "required": ["source_id", "doc_ref"],
    "additionalProperties": False,
}


# 引用规则：由宿主经官方 systemPrompt.section 写进系统提示（工具对该会话可见时才出现），描述里只留一句提示。
CITATION_GUIDANCE = (
    "引用规则：回答里用到知识检索或阅读的内容时，在对应句子末尾写上该证据的编号，格式为 [1]、[2]，"
    "编号只能取自 knowledge_search / knowledge_read 本会话返回过的 ref；多个出处连写 [1][3]。"
    "ref 为 null 的结果不能引用。不要编造编号，也不要写成 [ID:1]、[^1] 或带链接的形式；"
    # 实测（2026-10-22）：模型沿用前一轮的结果作答时自己从 [1] 编起脚注，[1] 落到了别的文档上
    "沿用前面轮次检索到的内容时，照样写当时那条结果的 ref；编号不要自己从 1 重新编，"
    "也不要在回答末尾另写脚注或「来源」说明，界面会按编号显示出处。"
    "知识里找不到依据时直接说明，不要凭常识补全后再标编号。"
    # 新旧版本：知识源识别出来的写在 version_note 里；没识别出来的靠文档日期兜底
    "版本规则：文档带 version_note 时照它说的办；几份文件对同一件事说法不一致时，以 date 较新的为准，"
    "并在回答里说明依据的是哪份文件。"
    # 回答里放图（照 KnowFlow 的图文混排）：模型只见短编号与描述，不见取图地址；前端按引用目录换成图片
    "图片规则：片段正文里的 <image id=\"IMG:3:0\"><caption>…</caption></image> 是一张图和它在原文里的位置，"
    "caption 是图注和图的内容；图没有图注和描述时给的是 <context>，那是图所在章节和图前后的原文，"
    "只用来判断这张图配哪段话，不是图里画的东西，不要据此描述图的内容；位置不明的图列在 images 里。"
    "某张图正好说明你正在写的那句话时，"
    "在那句话后面另起一行，原样写上 [IMG:3:0]。复述原文时图保持原文里的先后：原文在图前面的内容写在它前面，"
    "在图后面的写在它后面，不要把前后文合到一起再把图放到最后。只用结果里出现过的 id，不要改写格式，"
    "同一张图只放一次，不要把图都堆到回答最后，也不要照抄 <image> 标签、自己写图片链接或 <img>。"
    "和回答无关、装饰性的图不用放。")
# 放图提醒：带图的检索、阅读结果在 notes 末尾加这一句。图片规则只写在系统提示里时，离作答点隔着几万字检索结果，
# 模型约一半时候不放图；结果里补这一句后几乎每次都放，放的也对。2026-10-23 重放两轮真实对话（微短剧代表作品、
# 落地扇摆头方法）各 20 次，作答时放图从 13/19、10/19 到 20/20、20/20；只改规则写法、换成单一编号、调低温度都没用。
# 去重提示：同一轮里再次命中的片段只列编号。不说清楚，模型会以为这条结果是空的
SEEN_NOTE = "seen 为 true 的片段这一轮前面已经给过原文（同一个 ref），直接用前面那段内容和编号，不用为它再查。"
IMAGE_NOTE = ("这些结果里带有原文配图（<image id=…>）。回答用到某个片段的内容时，那段内容在原文里配的图要一起放进回答："
              "在对应文字后面另起一行，原样写上图的 id，如 [IMG:2:0]；和回答无关的图不放。")
# 一条片段里图片说明合计给到这么多字，超出的只留第一行（通常就是图注）。图一律插回原位、不按张数截：
# 补过相邻块的片段常有十几张图（说明书里的警示图标也在），按张数截会把正文后半段的图截掉（实测「风向调节」两张就这么丢了）
CAPTION_BUDGET = 2000
SHORT_CAPTION = 60


def _session_catalog(ctx: ToolContext) -> str:
    return ctx.session_id


async def _unbound(_ctx: ToolContext) -> TurnScope:
    return TurnScope()


def knowledge_tools(service: KnowledgeService, citations: CitationStore, *,
                    catalog_of: CatalogOf = _session_catalog,
                    turn_scope: TurnScopeOf = _unbound,
                    ledger: TurnLedger | None = None,
                    search_log: SearchLog | None = None,
                    sandbox: SessionInput | None = None) -> tuple[BuiltinTool, ...]:
    """catalog_of 决定一轮的引用目录 ID（默认按会话）；turn_scope 给出这一轮的范围（见 TurnScope），默认不限定；
    ledger 记这一轮给过原文的片段，再次命中时只列编号（见 turn_ledger），不给就不去重；
    search_log 记下每次检索给知识库运营用（见 search_log），不给就不记；
    sandbox 是会话沙箱，给了才有 knowledge_fetch（把图、页图、原文件放进工作区，见 fetch_tool）。"""

    def shown(ctx: ToolContext) -> frozenset[int]:
        return ledger.shown(ctx.session_id) if ledger is not None and ctx.from_root else frozenset()

    def remember(ctx: ToolContext, numbers: Sequence[int | None]) -> None:
        if ledger is not None and ctx.from_root:
            ledger.remember(ctx.session_id, frozenset(n for n in numbers if n is not None))

    async def search(ctx: ToolContext, arguments: dict[str, Any]) -> str:
        caller = await service.caller(ctx.workspace_id, ctx.user_id)
        scope = await turn_scope(ctx)
        requested = _within(tuple(str(x) for x in arguments.get("knowledge_bases") or ()), scope.bases)
        narrowed = _narrowed(_search_options(arguments), scope.dropped)
        outcome = await _guard(service.search(caller, str(arguments["query"]), top_n=_int(arguments.get("top_n"), 8),
                                              bases=requested, options=narrowed.options, pins=scope.pins))
        numbers, catalog = await citations.register(ctx.workspace_id, catalog_of(ctx), outcome.evidence)
        if search_log is not None:
            await search_log.record(ctx, query=str(arguments["query"]), evidence=outcome.evidence, numbers=numbers,
                                    trace=outcome.trace,
                                    options={"mode": narrowed.options.mode, "bases": list(requested or ()),
                                             "conditions": list(narrowed.applied)})
        # 同一步里并行的几次检索可能一条给原文、一条标 seen：几条结果一起进上下文，模型照样看得到原文。
        # 不会都标 seen：一次调用先取 shown 再 remember，谁标了对方，对方取 shown 时必然还没看到它
        seen = shown(ctx)
        payload: dict[str, Any] = {"documents": _grouped(outcome.evidence, numbers, seen), "notes": list(outcome.notes)}
        remember(ctx, numbers)
        if any(n in seen for n in numbers if n is not None):
            payload["notes"].append(SEEN_NOTE)
        # 用上的、撤掉的条件写在结果里：模型知道这次是怎么筛的，界面据此在回答上方列出来、让用户撤掉重答
        if narrowed.applied:
            payload["conditions"] = list(narrowed.applied)
        if narrowed.removed:
            payload["conditions_removed"] = list(narrowed.removed)
            payload["notes"].append(f"用户在这一轮撤掉了这些检索条件：{'；'.join(narrowed.removed)}。已经去掉，不要再加回来")
        if scope.pins:
            payload["notes"].append("检索已限定在用户这一轮 @ 点名的文件与标签里（点名的知识库只查这些），不用再自己加这些条件")
        if any(n is None for n in numbers):
            payload["notes"].append(f"本轮引用已达上限 {catalog.limit} 条，ref 为 null 的结果不能被引用")
        if not outcome.evidence:
            payload["notes"].append(_empty_note(narrowed.options))
        if _shows_images(outcome.evidence, [None if n in seen else n for n in numbers]):
            payload["notes"].append(IMAGE_NOTE)
        return json.dumps(payload, ensure_ascii=False)

    async def bases(ctx: ToolContext, arguments: dict[str, Any]) -> str:
        caller = await service.caller(ctx.workspace_id, ctx.user_id)
        requested = _within(tuple(str(x) for x in arguments.get("knowledge_bases") or ()), (await turn_scope(ctx)).bases)
        listing = await service.bases(caller, bases=requested)
        listed = listing.bases[:MAX_LISTED]
        notes = list(listing.notes)
        notes += [f"知识源「{name}」不支持按字段、标签筛选" for name in
                  dict.fromkeys(b.source_name for b in listed if not b.filterable)]
        if len(listing.bases) > len(listed):
            notes.append(f"另有 {len(listing.bases) - len(listed)} 个知识库没有列出，用 knowledge_bases 参数点名查看")
        payload = {"knowledge_bases": [_base(b) for b in listed], "filter_rules": FILTER_RULES, "notes": notes}
        return json.dumps(payload, ensure_ascii=False)

    async def documents(ctx: ToolContext, arguments: dict[str, Any]) -> str:
        caller = await service.caller(ctx.workspace_id, ctx.user_id)
        base = str(arguments["knowledge_base"])
        limit = max(1, min(_int(arguments.get("limit"), 20), MAX_LISTED_DOCUMENTS))
        page = Page(token=_text(arguments.get("cursor")), size=limit, query=_text(arguments.get("keyword")))
        listed, notes = await _guard(service.documents(caller, base, page, bases=(await turn_scope(ctx)).bases))
        source_id = base.partition(".")[0]
        payload: dict[str, Any] = {"knowledge_base": base, "source_id": source_id,
                                   "documents": [_document(d) for d in listed.items]}
        if listed.next_token:
            payload["next_cursor"] = listed.next_token
        if notes or not listed.items:
            payload["notes"] = [*notes, *(["没有对上的文档"] if not listed.items else [])]
        return json.dumps(payload, ensure_ascii=False)

    async def read(ctx: ToolContext, arguments: dict[str, Any]) -> str:
        caller = await service.caller(ctx.workspace_id, ctx.user_id)
        source_id, doc_ref = str(arguments["source_id"]), str(arguments["doc_ref"])
        chunk = await _chunk_of(citations, ctx.workspace_id, catalog_of(ctx), arguments.get("chunk"), source_id,
                                doc_ref)
        options = ReadOptions(section=_text(arguments.get("section")), chunk=chunk,
                              context=_int(arguments.get("context"), 1), find=_text(arguments.get("find")),
                              outline=arguments.get("outline") is True)
        result = await _guard(service.read(caller, source_id, doc_ref, cursor=arguments.get("cursor") or None,
                                           max_chars=_int(arguments.get("max_chars"), MAX_READ_CHARS),
                                           bases=(await turn_scope(ctx)).bases, options=options))
        if result.outline:
            payload = {"title": result.title, "doc_ref": result.doc_ref,
                       "outline": [_outline_item(node) for node in result.outline],
                       **({"notes": list(result.notes)} if result.notes else {})}
            return json.dumps(payload, ensure_ascii=False)
        numbers, _ = await citations.register(ctx.workspace_id, catalog_of(ctx), result.spans)
        remember(ctx, numbers)  # 读到的原文模型这一轮已经有了，之后再检索命中只列编号
        # 阅读的是一份文档：元数据在文档这一层给一次，不在每段重复
        meta = _metadata(result.spans[0]) if result.spans and result.spans[0].metadata else None
        payload = {"title": result.title, "doc_ref": result.doc_ref, **({"metadata": meta} if meta else {}),
                   "passages": _passages(result.spans, numbers),
                   "next_cursor": result.next_cursor.encode() if result.next_cursor else None}
        notes = [*result.notes, *([IMAGE_NOTE] if _shows_images(result.spans, numbers) else [])]
        if notes:
            payload["notes"] = notes
        return json.dumps(payload, ensure_ascii=False)

    async def bases_of(ctx: ToolContext) -> tuple[str, ...] | None:
        return (await turn_scope(ctx)).bases

    fetch = (fetch_tool(service, citations, sandbox, catalog_of=catalog_of, turn_bases=bases_of),) \
        if sandbox is not None else ()
    return (
        BuiltinTool(ToolSpec(SEARCH_NAME, "在本轮可查的知识库里检索。结果按文档分组：每份文档给一次 source_id、"
                                          "doc_ref、标题和元数据 metadata（字段：值），results 里是命中的片段，带引用编号 "
                                          "ref，回答引用时写 [ref]；这一轮前面已经给过原文的片段只列 ref 并标 seen。"
                                          "没找到时换成文档里会用的说法重写；同样的词换个 mode 再搜一般没用，编号、型号、"
                                          "报错码这类才改用 keyword。按标题或文件名找文档用 knowledge_documents。"
                                          "知识库目录里「可筛选」列了每个库能按哪些字段筛；要看可选值、标签集先调 "
                                          "knowledge_bases。",
                             SEARCH_SCHEMA, "read", guidance=CITATION_GUIDANCE, concurrent=True), search),
        BuiltinTool(ToolSpec(READ_NAME, f"按文档引用阅读原文，单次最多 {MAX_READ_CHARS} 字，用 next_cursor 续读。"
                                        "长文档先 outline=true 看目录，再用 section 填目录里的编号读那一节；编号以这次"
                                        "拿到的目录为准。回答引用时写段落的 [ref]。", READ_SCHEMA, "read",
                             guidance=CITATION_GUIDANCE, concurrent=True),
                    read),
        BuiltinTool(ToolSpec(BASES_NAME, "查看知识库的说明、元数据字段（类型、可选值）和标签，写 knowledge_search 的 "
                                         "filters、tags 之前用。", BASES_SCHEMA, "read", concurrent=True), bases),
        # 照 WeKnora 的 list_documents：看库里有哪些文档、按名字找文档。说清是知识库里的，免得和会话附件、工作区文件混
        BuiltinTool(ToolSpec(DOCUMENTS_NAME, "列出一个知识库里的文档（最近更新的在前），可以按标题或文件名里的字找。"
                                             "用来看库里有哪些文档、按名字找某份文档；doc_ref 交给 knowledge_read 读，"
                                             "outline 为 true 的可以先看目录。这是知识库里的文档，不是会话里上传的附件或"
                                             "工作区里的文件。", DOCUMENTS_SCHEMA, "read", concurrent=True),
                    documents),
        *fetch,
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


def _base(listed) -> dict:
    info = listed.info
    base: dict[str, Any] = {"id": listed.id, "name": info.name}
    if info.description:
        base["description"] = info.description
    if listed.filterable:
        base["fields"] = [field.model_dump(exclude_defaults=True) | {"type": field.type} for field in info.fields]
    return base


def _empty_note(options: SearchOptions) -> str:
    """没有结果时：用了范围条件就提醒可以放宽再查，免得模型直接回答「没有」。"""
    used = [name for value, name in ((options.folders, "文件夹"), (options.tags, "标签"), (options.filters, "元数据条件"))
            if value]
    if used:
        return f"加了{'、'.join(used)}限定后没有找到相关内容；条件可能太严，可以去掉或放宽后再查一次"
    return "在可读的知识范围内没有找到相关内容"


def _int(value: Any, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _strings(value: Any) -> tuple[str, ...]:
    return tuple(str(item).strip() for item in value if str(item).strip()) if isinstance(value, list) else ()


# 条件的固定写法：既是给模型、给用户看的文字，也是用户撤掉条件时交回来的标识（前后端都只认这一份）
_OP_TEXT = {"eq": "=", "ne": "≠", "gt": ">", "gte": "≥", "lt": "<", "lte": "≤", "contains": "包含"}


def condition_label(item: MetaFilter) -> str:
    if item.op == "in":
        return f"{item.field} 属于 {'、'.join(item.values)}"
    return f"{item.field} {_OP_TEXT.get(item.op, item.op)} {item.value}"


@dataclass(frozen=True)
class _Narrowed:
    options: SearchOptions
    applied: tuple[str, ...]  # 用上的条件
    removed: tuple[str, ...]  # 用户这一轮撤掉的条件


def _narrowed(options: SearchOptions, dropped: frozenset[str]) -> _Narrowed:
    """去掉用户这一轮撤掉的条件。元数据条件、每个标签、每个文件夹各算一条，可以单独撤。

    撤掉的条件每次检索都如数报回，模型这次没用到也报：模型知道整轮都别加回来，
    界面也一直能划掉显示，用户再撤别的条件时一并带上。"""
    filters = [(condition_label(item), item) for item in options.filters]
    tags = [(f"标签：{tag}", tag) for tag in options.tags]
    folders = [(f"文件夹：{folder}", folder) for folder in options.folders]
    labels = dict.fromkeys(label for label, _ in (*filters, *tags, *folders))  # 模型重复写的同一条只列一次
    kept = replace(options, filters=tuple(item for label, item in filters if label not in dropped),
                   tags=tuple(tag for label, tag in tags if label not in dropped),
                   folders=tuple(folder for label, folder in folders if label not in dropped))
    return _Narrowed(options=kept, applied=tuple(label for label in labels if label not in dropped),
                     removed=tuple(sorted(dropped)))


def _search_options(arguments: dict[str, Any]) -> SearchOptions:
    try:
        filters = tuple(MetaFilter.model_validate(item) for item in arguments.get("filters") or ())
    except (ValidationError, TypeError) as error:
        raise ToolFailure("filters 不合法：每个条件要有 field；in 用 values 给候选值，其余用 value") from error
    return SearchOptions(mode=str(arguments.get("mode") or "hybrid"), folders=_strings(arguments.get("folders")),
                         tags=_strings(arguments.get("tags")), filters=filters)


async def _chunk_of(citations: CitationStore, workspace_id: str, catalog_id: str, number: Any, source_id: str,
                    doc_ref: str) -> str | None:
    """模型给的是检索结果里的引用编号，换成知识源认得的片段引用；编号要属于这份文档。"""
    if number is None:
        return None
    entry = next((e for e in (await citations.load(workspace_id, catalog_id)).entries if e.number == number), None)
    if entry is None or (entry.evidence.source_id, entry.evidence.doc_ref) != (source_id, doc_ref):
        raise ToolFailure(f"引用编号 {number} 不是这份文档里的片段")
    return entry.evidence.span_ref


def _document(info: DocumentInfo) -> dict[str, Any]:
    """列文档时的一份：知识源给得出来的才写。"""
    item: dict[str, Any] = {"doc_ref": info.doc_ref, "title": info.title}
    if info.file_name and info.file_name != info.title:
        item["file"] = info.file_name
    if info.updated_at is not None:
        item["updated"] = info.updated_at.date().isoformat()
    if info.outline is not None:
        item["outline"] = info.outline
    return item


def _outline_item(node: OutlineNode) -> dict[str, Any]:
    """目录的一节给模型看的样子：编号、标题、页码（有才写）、字数，图表数不为 0 才写。"""
    item: dict[str, Any] = {"section": node.number, "title": node.title}
    if node.page_start is not None:
        end = node.page_end if node.page_end is not None else node.page_start
        item["pages"] = str(node.page_start) if end == node.page_start else f"{node.page_start}-{end}"
    item["chars"] = node.chars
    if node.figures:
        item["figures"] = node.figures
    if node.tables:
        item["tables"] = node.tables
    return item


def _metadata(evidence: Evidence) -> dict[str, str]:
    return {item.name: item.value for item in evidence.metadata}


def _passages(spans: Sequence[Evidence], numbers: Sequence[int]) -> list[dict[str, Any]]:
    """连续阅读的片段：章节变了才写一次 section，同一章连着的几段不重复，省得占篇幅。"""
    result: list[dict[str, Any]] = []
    previous: str | None = None
    for span, number in zip(spans, numbers, strict=True):
        section = _section(span)
        changed = {"section": section} if section and section != previous else {}
        text, images = _marked(span, number)
        result = [*result, {"ref": number, **changed, "text": text, **({"images": images} if images else {})}]
        previous = section
    return result


def _marked(evidence: Evidence, number: int | None) -> tuple[str, list[dict[str, str]]]:
    """片段正文与位置不明的图。图用短编号 IMG:<引用编号>:<第几张>：知识源给了位置的，以
    <image id=…><caption>…</caption></image> 插回正文原位（照 KnowFlow，模型看得到图前后是哪句话），
    没有图注和描述的图换成 <context>（图前后的原文，标开是为了不让模型当成图的内容）；
    没给位置的列在 images 里。没有引用编号的片段不能引用，图也不给；取图地址不给模型。"""
    if number is None:
        return evidence.text, []
    text = evidence.text
    labels = _labels(evidence.images)
    placed = [(image.at, index, image) for index, image in enumerate(evidence.images)
              if image.at is not None and image.at <= len(text)]
    for at, index, image in sorted(placed, key=lambda item: (item[0], item[1]), reverse=True):
        text = f'{text[:at]}<image id="IMG:{number}:{index}">{_explained(image, labels[index])}</image>{text[at:]}'
    seated = {index for _, index, _ in placed}
    rest = [{"id": f"IMG:{number}:{index}", ("caption" if image.caption or not image.context else "context"): labels[index]}
            for index, image in enumerate(evidence.images) if index not in seated]
    return text, rest


def _shows_images(spans: Sequence[Evidence], numbers: Sequence[int | None]) -> bool:
    """这次结果有没有图交给模型：没有引用编号的片段图不给（见 _marked）。"""
    return any(number is not None and span.images for span, number in zip(spans, numbers, strict=True))


def _labels(images: tuple[EvidenceImage, ...]) -> list[str]:
    """每张图交给模型的说明（图注与描述，没有时是前后原文）：按先后共用 CAPTION_BUDGET，超出的只留第一行。"""
    labels: list[str] = []
    spent = 0
    for image in images:
        label = image.caption or image.context
        if spent + len(label) > CAPTION_BUDGET:
            label = label.split("\n", 1)[0][:SHORT_CAPTION]
        spent += len(label)
        labels.append(label)
    return labels


def _explained(image: EvidenceImage, label: str) -> str:
    if image.caption:
        return f"<caption>{label}</caption>"
    return f"<context>{label}</context>" if image.context else ""


def _section(evidence: Evidence) -> str | None:
    """片段所在的章节（各级标题，知识源给了才有）。正文开头不一定写着标题，模型靠它知道这段属于哪一章；
    正文保持原文，不把标题拼进去（拼了高亮原文要跳过插入的行，块长也会超）。"""
    path = evidence.locator.section_path if evidence.locator else ()
    return " / ".join(path) if path else None


def _grouped(evidence: Sequence[Evidence], numbers: Sequence[int | None],
             seen: frozenset[int]) -> list[dict[str, Any]]:
    """检索结果按文档分组（照 WeKnora 的 writeKnowledgeMetadataHeader：文档一层的信息每份只写一次）。
    原来每条片段都带一遍知识源、知识库、文档引用、标题与元数据，占了结果的三成多。文档按最靠前那条命中的
    名次排，文档里的片段按名次排；这一轮给过原文的片段（seen）只列编号。"""
    documents: dict[tuple[str, str], dict[str, Any]] = {}
    for item, number in zip(evidence, numbers, strict=True):
        key = (item.source_id, item.doc_ref)
        entry = documents.get(key) or _doc_header(item)
        piece = {"ref": number, "seen": True} if number is not None and number in seen else _snippet(item, number)
        documents = {**documents, key: {**entry, "results": [*entry.get("results", ()), piece]}}
    return list(documents.values())


def _doc_header(evidence: Evidence) -> dict[str, Any]:
    """一份文档给模型看的信息：knowledge_read 要用的 source_id、doc_ref，筛选与回答要用的元数据。
    元数据与版本提示只在知识源给了时才出现，不给模型塞一堆空字段。"""
    header: dict[str, Any] = {"source_id": evidence.source_id, "doc_ref": evidence.doc_ref, "title": evidence.title}
    if evidence.kb_id:
        header["knowledge_base"] = base_id(evidence.source_id, evidence.kb_id)
    if evidence.metadata:
        header["metadata"] = _metadata(evidence)
    if evidence.version_note:
        header["version_note"] = evidence.version_note
    if evidence.document_date:
        header["date"] = evidence.document_date
    return header


def _snippet(evidence: Evidence, number: int | None) -> dict[str, Any]:
    locator = evidence.locator
    section = _section(evidence)
    text, images = _marked(evidence, number)
    piece: dict[str, Any] = {"ref": number, **({"section": section} if section else {})}
    if locator is not None and locator.page is not None:
        piece["page"] = locator.page
    return {**piece, "text": text, **({"images": images} if images else {})}
