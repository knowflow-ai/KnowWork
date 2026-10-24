"""本轮可查的知识库：范围收窄同检索；knowledge_bases 工具给说明、字段与条件规则（知识源不支持筛选时说明）；
每轮目录截短描述、太多时计数。"""

import json

import pytest

from knowwork_server.knowledge.catalog import MAX_DESCRIPTION, MAX_ENTRIES, turn_catalog
from knowwork_server.knowledge.models import BaseInfo, FieldInfo
from knowwork_server.knowledge.schemas import SourceCreate
from knowwork_server.knowledge.service import BaseListing, ListedBase
from knowwork_server.tools.registry import ToolContext

from .knowflow_stub import SERVICE_KEY

pytestmark = pytest.mark.anyio


async def setup_source(env):
    body = SourceCreate(name="合同库", base_url="http://kf.local", service_key=SERVICE_KEY,
                        config={"auth_mode": "assertion", "dataset_ids": ["ds1", "ds2"]})
    return await env.knowledge.admin.create("alice", "ws1", body)


async def test_bases_follow_grants_and_selection(env):
    source = await setup_source(env)
    alice = await env.knowledge.service.caller("ws1", "alice")
    listing = await env.knowledge.service.bases(alice)
    assert {b.id for b in listing.bases} == {f"{source.id}.ds1", f"{source.id}.ds2"}
    assert all(not b.filterable and b.source_name == "合同库" for b in listing.bases), "KnowFlow 不支持按字段筛"
    picked = await env.knowledge.service.bases(alice, bases=(f"{source.id}.ds2",))
    assert [b.id for b in picked.bases] == [f"{source.id}.ds2"]
    bob = await env.knowledge.service.caller("ws1", "bob")
    assert {b.id for b in (await env.knowledge.service.bases(bob)).bases} == {f"{source.id}.ds1"}
    erin = await env.knowledge.service.caller("ws1", "erin")
    nothing = await env.knowledge.service.bases(erin)
    assert nothing.bases == () and nothing.notes, "列不出来只说明原因，不报错"


async def test_bases_tool_explains_fields_and_unsupported_sources(env):
    source = await setup_source(env)
    tool = next(t for t in env.knowledge.tools if t.spec.name == "knowledge_bases")
    payload = json.loads(await tool.handler(ToolContext("ws1", "conv-1", "alice"), {}))
    assert {b["id"] for b in payload["knowledge_bases"]} == {f"{source.id}.ds1", f"{source.id}.ds2"}
    assert all("fields" not in b for b in payload["knowledge_bases"])
    assert "知识源「合同库」不支持按字段、标签筛选" in payload["notes"]
    assert "ne 不算没填这个字段的文档" in payload["filter_rules"]
    narrowed = json.loads(await tool.handler(ToolContext("ws1", "conv-1", "alice"),
                                             {"knowledge_bases": [f"{source.id}.ds1"]}))
    assert [b["id"] for b in narrowed["knowledge_bases"]] == [f"{source.id}.ds1"]


def listed(i, description="", fields=()):
    return ListedBase(id=f"builtin.kb{i}", source_name="扩展知识库", filterable=True,
                      info=BaseInfo(id=f"kb{i}", name=f"库{i}", description=description, fields=fields))


def test_turn_catalog_keeps_names_and_short_descriptions():
    long = "公司各类采购合同\n与补充协议。" * 40
    catalog = turn_catalog(BaseListing((listed(1, long, (FieldInfo(name="部门"),)),)))
    [entry] = catalog.bases
    assert (entry.id, entry.name) == ("builtin.kb1", "库1")
    assert len(entry.description) == MAX_DESCRIPTION and "\n" not in entry.description and entry.description[-1] == "…"
    many = turn_catalog(BaseListing(tuple(listed(i) for i in range(MAX_ENTRIES + 3))))
    assert len(many.bases) == MAX_ENTRIES and many.omitted == 3


def test_turn_catalog_lists_the_fields_each_base_can_be_filtered_by():
    """目录里给每个库附上能筛的字段（名字和类型）：原来只有名字和简介，要多调一次 knowledge_bases 才知道有哪些字段，
    模型多数时候直接搜、不筛。可选值和标签集照旧按需取，目录不变重。不支持筛选的知识源不列。"""
    fields = (FieldInfo(name="标签", multi=True, options=("合同", "制度")), FieldInfo(name="部门"),
              FieldInfo(name="签订日期", type="date"), FieldInfo(name="金额", type="number"))
    [entry] = turn_catalog(BaseListing((listed(1, fields=fields),))).bases
    assert entry.fields == ("标签（多值文本）", "部门（文本）", "签订日期（日期）", "金额（数字）")
    plain = ListedBase(id="ks_1.ds1", source_name="合同库", filterable=False,
                       info=BaseInfo(id="ds1", name="合同", fields=fields))
    assert turn_catalog(BaseListing((plain,))).bases[0].fields == ()
