import json

import pytest

from knowwork_server.knowledge import TurnScope, build_knowledge
from knowwork_server.knowledge.models import (
    Evidence,
    EvidenceImage,
    Locator,
    MetaFilter,
    MetaItem,
    OutlineNode,
    ReadResult,
)
from knowwork_server.knowledge.schemas import SourceCreate
from knowwork_server.knowledge.service import SearchOptions, SearchOutcome
from knowwork_server.knowledge.tools import (
    CAPTION_BUDGET,
    CITATION_GUIDANCE,
    IMAGE_NOTE,
    READ_SCHEMA,
    SEARCH_SCHEMA,
    _doc_header,
    _empty_note,
    _passages,
    _snippet,
    knowledge_tools,
)
from knowwork_server.tools.receipts import ReceiptStore
from knowwork_server.tools.registry import KW_NOW, StaticToolRegistry, ToolContext, ToolFailure
from knowwork_server.tools.service import ExecuteRequest, ToolService
from knowwork_server.tools.tokens import ToolTokenSigner

from .conftest import MASTER, flat
from .knowflow_stub import SERVICE_KEY

pytestmark = pytest.mark.anyio
# DSH 官方 dsh-tools 的 json-schema.js 接受的关键字；多一个就会在宿主注册时被拒
DSH_KEYWORDS = {"type", "oneOf", "properties", "required", "additionalProperties", "items", "enum", "const",
                "description", "title", "default", "examples"}


def keywords(node):
    if not isinstance(node, dict):
        return set()
    found = set(node)
    for child in (node.get("properties") or {}).values():
        found |= keywords(child)
    return found | keywords(node.get("items"))


def test_schemas_use_only_the_dsh_subset_and_names_fit_the_bridge():
    assert keywords(SEARCH_SCHEMA) <= DSH_KEYWORDS
    assert keywords(READ_SCHEMA) <= DSH_KEYWORDS


async def setup_source(env):
    body = SourceCreate(name="合同库", base_url="http://kf.local", service_key=SERVICE_KEY,
                        config={"auth_mode": "assertion", "dataset_ids": ["ds1", "ds2"]})
    return await env.knowledge.admin.create("alice", "ws1", body)


def tools_of(knowledge):
    return {tool.spec.name: tool for tool in knowledge.tools}


async def test_search_tool_registers_citations_and_respects_caller(env):
    source = await setup_source(env)
    tools = tools_of(env.knowledge)
    alice = json.loads(await tools["knowledge_search"].handler(ToolContext("ws1", "conv-1", "alice"),
                                                                {"query": "条款"}))
    assert [r["ref"] for r in flat(alice)] == [1, 2, 3]
    assert flat(alice)[0]["page"] == 1
    bob = json.loads(await tools["knowledge_search"].handler(ToolContext("ws1", "conv-2", "bob"), {"query": "条款"}))
    assert {r["doc_ref"] for r in flat(bob)} == {"ds1/contract"}
    assert {r["knowledge_base"] for r in flat(alice)} == {f"{source.id}.ds1", f"{source.id}.ds2"}
    narrowed = json.loads(await tools["knowledge_search"].handler(
        ToolContext("ws1", "conv-3", "alice"), {"query": "条款", "knowledge_bases": [f"{source.id}.ds2"]}))
    assert {r["doc_ref"] for r in flat(narrowed)} == {"ds2/secret"}
    check = await env.knowledge.citations.check_answer("ws1", "conv-2", "付款期限 30 天[1]，机密[3]。")
    assert check.text == "付款期限 30 天[1]，机密。" and check.removed == (3,)


async def test_search_tool_refuses_with_reason_when_nothing_is_readable(env):
    source = await setup_source(env)
    await env.access.grant("alice", "ws1", "user:erin", f"knowledge_base:{source.id}.*", "use")
    tool = tools_of(env.knowledge)["knowledge_search"]
    with pytest.raises(ToolFailure, match="没有对应账号"):
        await tool.handler(ToolContext("ws1", "c", "erin"), {"query": "x"})
    with pytest.raises(ToolFailure, match="检索词长度"):
        await tool.handler(ToolContext("ws1", "c", "alice"), {"query": "  "})


async def test_read_tool_returns_passages_with_refs_and_cursor(env):
    source = await setup_source(env)
    tool = tools_of(env.knowledge)["knowledge_read"]
    ctx = ToolContext("ws1", "conv-r", "alice")
    first = json.loads(await tool.handler(ctx, {"source_id": source.id, "doc_ref": "ds1/contract", "max_chars": 50}))
    assert first["title"] == "contract.pdf" and first["next_cursor"]
    assert [p["ref"] for p in first["passages"]] == [1, 2]
    again = json.loads(await tool.handler(ctx, {"source_id": source.id, "doc_ref": "ds1/contract",
                                                 "cursor": first["next_cursor"], "max_chars": 9000}))
    assert again["next_cursor"] is None
    assert [p["ref"] for p in again["passages"]] == [2, 3], "同一片段复用编号"


async def test_selection_limits_which_bases_the_model_may_name(env):
    source = await setup_source(env)

    async def bound(_ctx):
        return TurnScope(bases=(f"{source.id}.ds2", "ks_other.*"))

    knowledge = build_knowledge(MASTER, env.sessionmaker, env.access, client=env.knowledge.client,
                                turn_scope=bound)
    tools = tools_of(knowledge)
    ctx = ToolContext("ws1", "c", "alice")
    with pytest.raises(ToolFailure, match=f"知识库 {source.id}.ds1 不在本轮选择"):
        await tools["knowledge_search"].handler(ctx, {"query": "x", "knowledge_bases": [f"{source.id}.ds1"]})
    with pytest.raises(ToolFailure, match="不在本轮选择"):
        await tools["knowledge_search"].handler(ctx, {"query": "x", "knowledge_bases": [f"{source.id}.*"]})
    with pytest.raises(ToolFailure, match="不在本轮选择"):
        await tools["knowledge_read"].handler(ctx, {"source_id": source.id, "doc_ref": "ds1/contract"})
    hits = json.loads(await tools["knowledge_search"].handler(ctx, {"query": "x"}))
    assert {r["knowledge_base"] for r in flat(hits)} == {f"{source.id}.ds2"}, "没点名就用本轮选择"
    assert any("ks_other" in note for note in hits["notes"])


async def test_documents_tool_lists_one_knowledge_base_within_the_turn_scope(env):
    """knowledge_documents（照 WeKnora 的 list_documents）：列一个知识库里的文档，给 knowledge_read 用的 doc_ref；
    知识源不支持按名字找时忽略 keyword 并说明；要指定一个知识库，并且在授权与本轮选择范围内。"""
    source = await setup_source(env)
    tool = tools_of(env.knowledge)["knowledge_documents"]
    assert "附件" in tool.spec.description, "说清是知识库里的文档，不是会话附件或工作区文件"
    ctx = ToolContext("ws1", "conv-docs", "alice")
    listed = json.loads(await tool.handler(ctx, {"knowledge_base": f"{source.id}.ds1"}))
    assert listed["source_id"] == source.id and [d["doc_ref"] for d in listed["documents"]] == ["ds1/contract"]
    assert "next_cursor" not in listed
    named = json.loads(await tool.handler(ctx, {"knowledge_base": f"{source.id}.ds1", "keyword": "合同"}))
    assert named["documents"] and "按名字找" in named["notes"][0]
    with pytest.raises(ToolFailure, match="一个知识库"):
        await tool.handler(ctx, {"knowledge_base": f"{source.id}.*"})

    async def bound(_ctx):
        return TurnScope(bases=(f"{source.id}.ds2",))

    scoped = build_knowledge(MASTER, env.sessionmaker, env.access, client=env.knowledge.client, turn_scope=bound)
    with pytest.raises(ToolFailure, match="不在本轮选择"):
        await tools_of(scoped)["knowledge_documents"].handler(ctx, {"knowledge_base": f"{source.id}.ds1"})


async def test_citation_store_truncates_explicitly(env):
    knowledge = build_knowledge(MASTER, env.sessionmaker, env.access, client=env.knowledge.client, citation_limit=2)
    evidences = [Evidence(source_id="s", doc_ref="d", span_ref=f"p{i}", title="t", text=str(i)) for i in range(3)]
    numbers, catalog = await knowledge.citations.register("ws1", "turn-1", evidences)
    assert numbers == (1, 2, None) and catalog.truncated and catalog.dropped == 1
    numbers, _ = await knowledge.citations.register("ws1", "turn-1", evidences[1:2])
    assert numbers == (2,)
    loaded = await knowledge.citations.load("ws1", "turn-1")
    assert [e.number for e in loaded.entries] == [1, 2] and loaded.truncated
    assert any("截断" in w for w in loaded.check("[1]").warnings)
    assert (await knowledge.citations.load("ws1", "nothing")).entries == ()
    with pytest.raises(PermissionError):
        await knowledge.citations.register("ws2", "turn-1", evidences)


async def test_tools_run_through_the_tool_bridge_with_user_from_token(env):
    await setup_source(env)
    registry = StaticToolRegistry.of([KW_NOW, *env.knowledge.tools])
    service = ToolService(registry, ToolTokenSigner.from_master_key(MASTER), ReceiptStore(env.sessionmaker))
    token = await service.issue_session_token("ws1", "conv-b", user_id="bob")
    claims = service.verify(token)
    assert {"knowledge_search", "knowledge_read"} <= claims.tools
    result = await service.execute(claims, ExecuteRequest(tool_name="knowledge_search", arguments={"query": "条款"},
                                                          agent_session_id="conv-b", call_key="conv-b:call-1"))
    assert result.status == "succeeded"
    assert {r["doc_ref"] for r in flat(json.loads(result.content))} == {"ds1/contract"}


async def test_discard_removes_the_whole_catalog(env):
    evidences = [Evidence(source_id="s", doc_ref="d", span_ref=f"p{i}", title="t", text=str(i)) for i in range(2)]
    await env.knowledge.citations.register("ws1", "turn-gone", evidences)
    await env.knowledge.citations.discard("ws1", "turn-gone")
    assert (await env.knowledge.citations.load("ws1", "turn-gone")).entries == ()


def test_hits_carry_images_and_version_note_only_when_present():
    plain = Evidence(source_id="s", doc_ref="d", span_ref="p", title="t", text="x")
    assert "images" not in _snippet(plain, 1) and "version_note" not in _doc_header(plain)
    assert "date" not in _doc_header(plain)
    rich = plain.model_copy(update={"images": (EvidenceImage(ref="i1", caption="流程图", url="/x"),),
                                    "version_note": "已有更新版本", "document_date": "2025-03-01"})
    assert _snippet(rich, 3)["images"] == [{"id": "IMG:3:0", "caption": "流程图"}], "短编号加描述，取图地址不给模型"
    assert "images" not in _snippet(rich, None), "没有引用编号的片段不能引用，图也放不进回答"
    assert _doc_header(rich)["version_note"] == "已有更新版本"
    assert _doc_header(rich)["date"] == "2025-03-01", "几份文件说法不一致时模型据此判断哪份较新"
    assert "version_note" in CITATION_GUIDANCE and "date 较新的为准" in CITATION_GUIDANCE


class ImageService:
    """只回检索、阅读结果的知识服务替身：片段带不带图由测试给。"""

    def __init__(self, evidence: tuple[Evidence, ...]) -> None:
        self.evidence = evidence

    async def caller(self, workspace_id, user_id):
        return None

    async def search(self, caller, query, **_):
        return SearchOutcome(evidence=self.evidence)

    async def read(self, caller, source_id, doc_ref, **_):
        return ReadResult(doc_ref=doc_ref, title="说明书", text="", spans=self.evidence)


async def results_of(citations, evidence, conversation):
    tools = {tool.spec.name: tool for tool in knowledge_tools(ImageService(evidence), citations)}
    ctx = ToolContext("ws1", conversation, "alice")
    found = json.loads(await tools["knowledge_search"].handler(ctx, {"query": "摆头"}))
    read = json.loads(await tools["knowledge_read"].handler(ctx, {"source_id": "s", "doc_ref": "d"}))
    return found, read


async def test_results_with_images_remind_the_model_to_place_them(env):
    """带图的检索、阅读结果在 notes 末尾提醒放图：规则只写在系统提示里时，离作答点隔着几万字检索结果，
    模型约一半时候不放图。不带图的结果不加这句；引用满了、没拿到编号的片段图本来就不给模型，也不加。"""
    swing = Evidence(source_id="s", doc_ref="d", span_ref="swing", title="说明书", text="摆头方法",
                     images=(EvidenceImage(ref="lever", caption="摆头拉杆", at=0),))
    timer = Evidence(source_id="s", doc_ref="d", span_ref="timer", title="说明书", text="定时关机")
    found, read = await results_of(env.knowledge.citations, (timer, swing), "conv-img")
    assert found["notes"] == [IMAGE_NOTE] and read["notes"] == [IMAGE_NOTE]
    found, read = await results_of(env.knowledge.citations, (timer,), "conv-plain")
    assert found["notes"] == [] and "notes" not in read
    full = build_knowledge(MASTER, env.sessionmaker, env.access, client=env.knowledge.client, citation_limit=1)
    found, read = await results_of(full.citations, (timer, swing), "conv-full")
    assert IMAGE_NOTE not in found["notes"] and "notes" not in read


class OutlineService(ImageService):
    """阅读时回目录：记下收到的阅读参数。"""

    def __init__(self) -> None:
        super().__init__(())
        self.asked: list[dict] = []

    async def read(self, caller, source_id, doc_ref, **kwargs):
        self.asked.append(kwargs)
        return ReadResult(doc_ref=doc_ref, title="说明书", text="", outline=(
            OutlineNode(number="1", title="各部件的名称与组装方法", level=1, page_start=6, page_end=7, chars=820),
            OutlineNode(number="1.1", title="组装底座", level=2, page_start=6, page_end=6, chars=300, figures=2,
                        tables=1),
            OutlineNode(number="2", title="规格", level=1, chars=120),
        ))


async def test_read_tool_returns_an_outline_to_read_by_section_number(env):
    """先看目录（每节编号、页码、字数，有图表才写图表数），再按编号读那一节。"""
    assert "outline" in READ_SCHEMA["properties"] and "编号" in READ_SCHEMA["properties"]["section"]["description"]
    service = OutlineService()
    tool = {t.spec.name: t for t in knowledge_tools(service, env.knowledge.citations)}["knowledge_read"]
    payload = json.loads(await tool.handler(ToolContext("ws1", "conv-outline", "alice"),
                                            {"source_id": "s", "doc_ref": "d", "outline": True, "section": "1"}))
    assert service.asked[0]["options"].outline is True and service.asked[0]["options"].section == "1"
    assert payload == {"title": "说明书", "doc_ref": "d", "outline": [
        {"section": "1", "title": "各部件的名称与组装方法", "pages": "6-7", "chars": 820},
        {"section": "1.1", "title": "组装底座", "pages": "6", "chars": 300, "figures": 2, "tables": 1},
        {"section": "2", "title": "规格", "chars": 120},
    ]}


def test_hits_carry_the_documents_metadata():
    """结果里每份文档带上它的元数据（字段：值）：问「这份合同哪年签的」「哪些属于财务部」，模型不用从正文里猜，
    也核对得了筛选条件生效没有。没有元数据的知识源不带这一项。"""
    plain = Evidence(source_id="s", doc_ref="d", span_ref="p", title="合同", text="付款期限 30 天")
    assert "metadata" not in _doc_header(plain)
    tagged = plain.model_copy(update={"metadata": (MetaItem(name="部门", value="财务部"),
                                                   MetaItem(name="签订日期", value="2025-03-01"),
                                                   MetaItem(name="标签", value="合同、采购"))})
    assert _doc_header(tagged)["metadata"] == {"部门": "财务部", "签订日期": "2025-03-01", "标签": "合同、采购"}


def test_hits_say_which_section_they_are_in_without_touching_the_text():
    """章节路径单独给：正文开头不一定写着标题，模型靠它知道这段属于哪一章；正文保持原文。"""
    plain = Evidence(source_id="s", doc_ref="d", span_ref="p", title="t", text="住宿每晚 500 元")
    assert "section" not in _snippet(plain, 1)
    placed = plain.model_copy(update={"locator": Locator(type="pdf", page=3, section_path=("第二章 费用", "2.1 住宿"))})
    hit = _snippet(placed, 1)
    assert (hit["section"], hit["page"], hit["text"]) == ("第二章 费用 / 2.1 住宿", 3, "住宿每晚 500 元")


def test_images_go_back_to_where_they_were_in_the_text():
    """知识源给了图的位置：图标记插回正文原位（模型看得到图前后是哪句话），没给位置的仍列在 images 里。"""
    text = "报销分三步。先填申请单。审批后报销。"
    hit = _snippet(Evidence(source_id="s", doc_ref="d", span_ref="p", title="t", text=text, images=(
        EvidenceImage(ref="a", caption="报销流程图", at=6),
        EvidenceImage(ref="b", at=len(text)),
        EvidenceImage(ref="c", caption="位置不明的图"),
        EvidenceImage(ref="d", caption="位置越界", at=999),
    )), 2)
    assert hit["text"] == ('报销分三步。<image id="IMG:2:0"><caption>报销流程图</caption></image>先填申请单。审批后报销。'
                           '<image id="IMG:2:1"></image>')
    assert hit["images"] == [{"id": "IMG:2:2", "caption": "位置不明的图"}, {"id": "IMG:2:3", "caption": "位置越界"}]
    same = Evidence(source_id="s", doc_ref="d", span_ref="p", title="t", text="ab",
                    images=(EvidenceImage(ref="x", at=1), EvidenceImage(ref="y", at=1)))
    assert _snippet(same, 1)["text"] == 'a<image id="IMG:1:0"></image><image id="IMG:1:1"></image>b', "同一处的图保持先后"
    assert _snippet(same, None)["text"] == "ab", "没有引用编号的片段不插图"


def test_images_without_caption_or_description_carry_the_text_around_them_apart():
    """没有图注和描述的图，知识源给的前后原文放在 <context> 里，和 <caption> 分开，模型不会当成图的内容。"""
    hit = _snippet(Evidence(source_id="s", doc_ref="d", span_ref="p", title="t", text="插头插好。不插好会触电。", images=(
        EvidenceImage(ref="plug", context="前文：插头插好。", at=5),
        EvidenceImage(ref="lost", context="前文：插头插好。"),
        EvidenceImage(ref="bare"),
    )), 4)
    assert hit["text"] == '插头插好。<image id="IMG:4:0"><context>前文：插头插好。</context></image>不插好会触电。'
    assert hit["images"] == [{"id": "IMG:4:1", "context": "前文：插头插好。"}, {"id": "IMG:4:2", "caption": ""}]


def test_every_image_is_placed_and_long_descriptions_share_a_budget():
    """一条片段里的图全部插回原位，不按张数截（实测补过相邻块的片段有 12 张图，正文后半段「风向调节」的两张
    排在第 8、9 张被截掉了）；描述按片段的总字数预算给，超出的只留第一行（通常就是图注）。"""
    long = "部件示意图\n" + "马达部、升降杆、高度调节按钮、支架、电源线、电源插头与底座的位置。" * 40
    early = tuple(EvidenceImage(ref=f"part{i}", caption=long, at=0) for i in range(6))
    crowded = Evidence(source_id="s", doc_ref="d", span_ref="p", title="t", text="组装说明。风向调节的方法。",
                       images=(*early, EvidenceImage(ref="wind", caption="左右风向调节\n约45° 约45°", at=5),
                               EvidenceImage(ref="sign", context="前文：可动部位不可把手指伸入。", at=13)))
    text = _snippet(crowded, 2)["text"]
    assert text.count("<image") == 8, "图全部插回原位"
    assert f"<caption>{long}</caption>" in text, "预算内给完整描述"
    assert text.count("<caption>部件示意图</caption>") == 5, "超出预算的只留第一行"
    assert '<image id="IMG:2:6"><caption>左右风向调节\n约45° 约45°</caption></image>风向调节的方法' in text
    assert '<image id="IMG:2:7"><context>前文：可动部位不可把手指伸入。</context></image>' in text
    assert len(text) < len(long) + CAPTION_BUDGET + 400


def test_read_passages_name_the_section_only_when_it_changes():
    def span(text, *path):
        locator = Locator(type="text", section_path=path) if path else None
        return Evidence(source_id="s", doc_ref="d", span_ref=text, title="t", text=text, locator=locator)

    passages = _passages([span("a", "第一章"), span("b", "第一章"), span("c"), span("d", "第一章"),
                          span("e", "第二章")], [1, 2, 3, 4, 5])
    assert [p.get("section") for p in passages] == ["第一章", None, None, "第一章", "第二章"]
    assert [p["text"] for p in passages] == ["a", "b", "c", "d", "e"]



def test_empty_results_suggest_loosening_the_conditions_that_were_used():
    assert _empty_note(SearchOptions()) == "在可读的知识范围内没有找到相关内容"
    note = _empty_note(SearchOptions(tags=("合同",), filters=(MetaFilter(field="部门", value="财务"),)))
    assert note.startswith("加了标签、元数据条件限定后没有找到") and "放宽" in note
