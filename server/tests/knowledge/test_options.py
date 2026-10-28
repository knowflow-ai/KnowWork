"""检索与阅读的可选参数：请求对象的校验；知识源不支持的参数去掉并写明，支持的原样交给知识源；
阅读时模型给引用编号，换成知识源认得的片段引用。"""

import json

import pytest
from pydantic import ValidationError

from knowwork_server.knowledge import ProvidedSource
from knowwork_server.knowledge.connectors import ConnectorFactory
from knowwork_server.knowledge.models import BaseInfo, Evidence, MetaFilter, Pin, ReadRequest, Scope, SearchRequest
from knowwork_server.knowledge.schemas import SourceCreate
from knowwork_server.knowledge.service import Pinned, ReadOptions, SearchOptions
from knowwork_server.knowledge.tools import TurnScope, knowledge_tools
from knowwork_server.tools.registry import ToolContext, ToolFailure

from .conftest import flat
from .knowflow_stub import SERVICE_KEY

pytestmark = pytest.mark.anyio
SCOPE = Scope(dataset_ids=("ds1",))


def test_filters_use_value_or_values_by_operator():
    assert MetaFilter(field="年份", op="gte", value="2024").value == "2024"
    assert MetaFilter(field="部门", op="in", values=("法务", "财务")).values == ("法务", "财务")
    for bad in ({"field": "部门", "op": "in", "value": "法务"}, {"field": "年份", "op": "eq"},
                {"field": "年份", "op": "lt", "value": "1", "values": ["2"]}, {"field": "", "value": "x"}):
        with pytest.raises(ValidationError):
            MetaFilter.model_validate(bad)


def test_requests_report_which_optional_features_they_use():
    assert SearchRequest(query="q", scope=SCOPE).wants() == frozenset()
    rich = SearchRequest(query="q", scope=SCOPE, mode="keyword", folders=("合同",),
                         filters=(MetaFilter(field="年份", value="2024"),))
    assert rich.wants() == {"mode", "folders", "filters"}
    assert ReadRequest(doc_ref="d", find="违约金").wants() == {"find"}
    with pytest.raises(ValidationError, match="只能选一个"):
        ReadRequest(doc_ref="d", section="第三章", find="x")


def test_outline_can_narrow_to_a_section_but_not_go_with_chunk_or_find():
    """看目录：整本的，或配 section 只看那一章下面的子目录；和「从片段读」「文内查找」不能一起用。"""
    assert ReadRequest(doc_ref="d", outline=True).wants() == {"outline"}
    assert ReadRequest(doc_ref="d", outline=True, section="2").wants() == {"outline", "section"}
    for other in ({"chunk": "c1"}, {"find": "违约金"}):
        with pytest.raises(ValidationError, match="目录"):
            ReadRequest(doc_ref="d", outline=True, **other)


async def setup_source(env):
    body = SourceCreate(name="合同库", base_url="http://kf.local", service_key=SERVICE_KEY,
                        config={"auth_mode": "assertion", "dataset_ids": ["ds1", "ds2"]})
    return await env.knowledge.admin.create("alice", "ws1", body)


async def test_unsupported_search_options_are_dropped_and_noted(env):
    await setup_source(env)
    caller = await env.knowledge.service.caller("ws1", "alice")
    options = SearchOptions(mode="keyword", tags=("已签",), filters=(MetaFilter(field="年份", value="2024"),))
    outcome = await env.knowledge.service.search(caller, "条款", options=options)
    assert outcome.evidence, "照常检索"
    assert outcome.notes == ("知识源「合同库」不支持指定检索方式、按标签检索、按元数据条件检索，这些条件已忽略",)
    body = json.loads(next(r for r in env.stub.requests if r.url.path == "/api/v1/retrieval").read())
    assert not {"mode", "tags", "filters"} & set(body), "不支持的条件不发给知识源"


async def test_supported_options_reach_the_source_untouched(env, monkeypatch):
    await setup_source(env)
    seen = []
    original = ConnectorFactory.build

    def build(self, record):
        source = original(self, record)
        source.features = frozenset({"mode", "folders", "find"})
        search, read = source.search, source.read

        async def spy_search(caller, request):
            seen.append(request)
            return await search(caller, request)

        async def spy_read(caller, request):
            seen.append(request)
            return await read(caller, request.model_copy(update={"find": None}))

        source.search, source.read = spy_search, spy_read
        return source

    monkeypatch.setattr(ConnectorFactory, "build", build)
    caller = await env.knowledge.service.caller("ws1", "alice")
    outcome = await env.knowledge.service.search(caller, "条款", options=SearchOptions(mode="keyword",
                                                                                     folders=("合同/2025",),
                                                                                     tags=("已签",)))
    assert (seen[0].mode, seen[0].folders, seen[0].tags) == ("keyword", ("合同/2025",), ())
    assert outcome.notes == ("知识源「合同库」不支持按标签检索，这些条件已忽略",)
    source_id = outcome.evidence[0].source_id
    result = await env.knowledge.service.read(caller, source_id, "ds1/contract", options=ReadOptions(find="付款"))
    assert seen[1].find == "付款" and result.notes == ()


async def test_unsupported_read_mode_falls_back_to_sequential_reading_with_a_note(env):
    source = await setup_source(env)
    caller = await env.knowledge.service.caller("ws1", "alice")
    result = await env.knowledge.service.read(caller, source.id, "ds1/contract", options=ReadOptions(find="付款"))
    assert result.text and result.notes == ("知识源「合同库」不支持在文档内查找，这些条件已忽略，已从开头顺序阅读",)


async def test_a_source_without_outlines_reads_from_the_start_and_says_so(env):
    source = await setup_source(env)
    caller = await env.knowledge.service.caller("ws1", "alice")
    result = await env.knowledge.service.read(caller, source.id, "ds1/contract", options=ReadOptions(outline=True))
    assert result.text and result.outline == ()
    assert result.notes == ("知识源「合同库」不支持查看目录，这些条件已忽略，已从开头顺序阅读",)


def tools_of(knowledge):
    return {tool.spec.name: tool for tool in knowledge.tools}


async def test_tools_pass_options_and_turn_chunk_refs_into_passages(env):
    source = await setup_source(env)
    tools = tools_of(env.knowledge)
    ctx = ToolContext("ws1", "conv-o", "alice")
    found = json.loads(await tools["knowledge_search"].handler(ctx, {
        "query": "条款", "mode": "keyword", "filters": [{"field": "年份", "op": "in", "values": ["2024"]}]}))
    assert any("不支持" in note for note in found["notes"])
    with pytest.raises(ToolFailure, match="filters 不合法"):
        await tools["knowledge_search"].handler(ctx, {"query": "条款", "filters": [{"field": "年份", "op": "in"}]})
    ref_of = {hit["doc_ref"]: hit["ref"] for hit in flat(found)}
    read = json.loads(await tools["knowledge_read"].handler(ctx, {
        "source_id": source.id, "doc_ref": "ds1/contract", "chunk": ref_of["ds1/contract"], "context": 2}))
    assert read["passages"] and "不支持从指定片段读" in read["notes"][0]
    with pytest.raises(ToolFailure, match="不是这份文档里的片段"):
        await tools["knowledge_read"].handler(ctx, {"source_id": source.id, "doc_ref": "ds1/contract",
                                                    "chunk": ref_of["ds2/secret"]})
    with pytest.raises(ToolFailure, match="只能选一个"):
        await tools["knowledge_read"].handler(ctx, {"source_id": source.id, "doc_ref": "ds1/contract",
                                                    "section": "第一章", "find": "付款"})


class Conditional:
    """支持全部条件、记下收到什么的知识源。"""

    source_id = "builtin"
    features = frozenset({"filters", "tags", "folders"})

    def __init__(self):
        self.requests = []

    async def list_bases(self, caller):
        return (BaseInfo(id="kb1", name="制度库"),)

    def base_of(self, doc_ref):
        return "kb1"

    async def search(self, caller, request):
        self.requests.append(request)
        return [Evidence(source_id="builtin", kb_id="kb1", doc_ref="kb1/d1", span_ref="s1", title="制度",
                         text="差旅按标准报销")]

    async def read(self, caller, request):
        raise NotImplementedError

    async def list_documents(self, caller, scope, page):
        raise NotImplementedError

    async def probe(self, caller):
        return None


async def test_conditions_the_user_dropped_are_removed_and_both_lists_are_reported(env):
    """每个元数据条件、每个标签、每个文件夹各算一条，用户可以单独撤；撤掉的不交给知识源，结果里写明，
    并提醒模型不要再加回来。条件的写法就是撤销时交回来的标识。"""
    source = Conditional()
    env.knowledge.provided.add(ProvidedSource(id="builtin", name="扩展知识库", kind="builtin", source=source))

    async def dropped(_ctx):
        return TurnScope(dropped=frozenset({"部门 属于 法务、财务", "标签：草稿"}))

    [search] = [t for t in knowledge_tools(env.knowledge.service, env.knowledge.citations,
                                           turn_scope=dropped) if t.spec.name == "knowledge_search"]
    payload = json.loads(await search.handler(ToolContext("ws1", "conv-d", "alice"), {
        "query": "报销", "folders": ["合同"], "tags": ["已签", "草稿"],
        "filters": [{"field": "部门", "op": "in", "values": ["法务", "财务"]},
                    {"field": "年份", "op": "gte", "value": "2024"}, {"field": "地区", "op": "contains", "value": "华东"}]}))
    [request] = source.requests
    assert [(f.field, f.op) for f in request.filters] == [("年份", "gte"), ("地区", "contains")]
    assert (request.tags, request.folders) == (("已签",), ("合同",))
    assert payload["conditions"] == ["年份 ≥ 2024", "地区 包含 华东", "标签：已签", "文件夹：合同"]
    assert payload["conditions_removed"] == ["标签：草稿", "部门 属于 法务、财务"]
    assert any("不要再加回来" in note for note in payload["notes"])
    plain = json.loads(await search.handler(ToolContext("ws1", "conv-d", "alice"), {"query": "报销"}))
    assert "conditions" not in plain and plain["conditions_removed"] == ["标签：草稿", "部门 属于 法务、财务"], \
        "这次没用到也报回：整轮都别加回来"


async def test_plain_searches_carry_no_condition_lists(env):
    source = Conditional()
    env.knowledge.provided.add(ProvidedSource(id="builtin", name="扩展知识库", kind="builtin", source=source))
    payload = json.loads(await tools_of(env.knowledge)["knowledge_search"].handler(
        ToolContext("ws1", "conv-p", "alice"), {"query": "报销"}))
    assert payload["documents"] and not {"conditions", "conditions_removed"} & set(payload)
    with_one = json.loads(await tools_of(env.knowledge)["knowledge_search"].handler(
        ToolContext("ws1", "conv-p", "alice"), {"query": "报销", "filters": [{"field": "部门", "op": "ne", "value": "人事"}]}))
    assert with_one["conditions"] == ["部门 ≠ 人事"] and "conditions_removed" not in with_one


class Pinnable(Conditional):
    """两个库；features 可以换掉，模拟不支持点名的知识源。"""

    def __init__(self, features=frozenset({"pins"}), source_id="builtin"):
        super().__init__()
        self.features = features
        self.source_id = source_id

    async def list_bases(self, caller):
        return (BaseInfo(id="kb1", name="制度库"), BaseInfo(id="kb2", name="合同库"))

    def base_of(self, doc_ref):
        return doc_ref.split("/", 1)[0]


def pinned_search(env, source, scope):
    env.knowledge.provided.add(ProvidedSource(id=source.source_id, name="扩展知识库", kind="builtin", source=source))

    async def turn(_ctx):
        return scope

    [search] = [t for t in knowledge_tools(env.knowledge.service, env.knowledge.citations, turn_scope=turn)
                if t.spec.name == "knowledge_search"]
    return search


async def test_pins_reach_sources_that_support_them(env):
    """@ 点名的文件与标签交给知识源，点名的库只查它们；结果里告诉模型已经限定，不用自己再加条件。"""
    source = Pinnable()
    scope = TurnScope(bases=("builtin.kb1", "builtin.kb2"),
                      pins=(Pinned(base="builtin.kb1", documents=("kb1/d1",), tags=("合同",)),))
    payload = json.loads(await pinned_search(env, source, scope).handler(ToolContext("ws1", "c-p", "alice"),
                                                                         {"query": "报销"}))
    [request] = source.requests
    assert request.scope.dataset_ids == ("kb1", "kb2")
    assert request.scope.pins == (Pin(dataset_id="kb1", document_ids=("kb1/d1",), tags=("合同",)),)
    assert any("@ 点名" in note for note in payload["notes"])


async def test_sources_without_pins_never_widen_the_users_choice(env):
    """不支持点名的知识源：都只点名了文档时合起来按文档查；做不到的那几个库这次不查，并写明原因。"""
    plain = Pinnable(features=frozenset())
    docs_only = TurnScope(bases=("builtin.kb1", "builtin.kb2"),
                          pins=(Pinned(base="builtin.kb1", documents=("kb1/d1",)),
                                Pinned(base="builtin.kb2", documents=("kb2/d9",))))
    await pinned_search(env, plain, docs_only).handler(ToolContext("ws1", "c-q", "alice"), {"query": "报销"})
    [request] = plain.requests
    assert (request.scope.document_ids, request.scope.pins) == (("kb1/d1", "kb2/d9"), ())

    tagged = Pinnable(features=frozenset(), source_id="other")
    mixed = TurnScope(bases=("other.kb1", "other.kb2"), pins=(Pinned(base="other.kb1", tags=("合同",)),))
    payload = json.loads(await pinned_search(env, tagged, mixed).handler(ToolContext("ws1", "c-r", "alice"),
                                                                         {"query": "报销"}))
    [request] = tagged.requests
    assert request.scope.dataset_ids == ("kb2",), "只点名了标签的库查不了，就不查，不当成整库查"
    assert any("不支持只查 @ 点名" in note for note in payload["notes"])
