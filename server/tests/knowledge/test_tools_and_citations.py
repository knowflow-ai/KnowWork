import json

import pytest

from knowwork_server.knowledge import build_knowledge
from knowwork_server.knowledge.models import Evidence
from knowwork_server.knowledge.schemas import SourceCreate
from knowwork_server.knowledge.tools import READ_SCHEMA, SEARCH_SCHEMA
from knowwork_server.tools.receipts import ReceiptStore
from knowwork_server.tools.registry import KW_NOW, StaticToolRegistry, ToolContext, ToolFailure
from knowwork_server.tools.service import ExecuteRequest, ToolService
from knowwork_server.tools.tokens import ToolTokenSigner

from .conftest import MASTER
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
    assert [r["ref"] for r in alice["results"]] == [1, 2, 3]
    assert alice["results"][0]["page"] == 1
    bob = json.loads(await tools["knowledge_search"].handler(ToolContext("ws1", "conv-2", "bob"), {"query": "条款"}))
    assert {r["doc_ref"] for r in bob["results"]} == {"ds1/contract"}
    assert {r["knowledge_base"] for r in alice["results"]} == {f"{source.id}.ds1", f"{source.id}.ds2"}
    narrowed = json.loads(await tools["knowledge_search"].handler(
        ToolContext("ws1", "conv-3", "alice"), {"query": "条款", "knowledge_bases": [f"{source.id}.ds2"]}))
    assert {r["doc_ref"] for r in narrowed["results"]} == {"ds2/secret"}
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
        return (f"{source.id}.ds2", "ks_other.*")

    knowledge = build_knowledge(MASTER, env.sessionmaker, env.access, client=env.knowledge.client,
                                bound_bases=bound)
    tools = tools_of(knowledge)
    ctx = ToolContext("ws1", "c", "alice")
    with pytest.raises(ToolFailure, match=f"知识库 {source.id}.ds1 不在本轮选择"):
        await tools["knowledge_search"].handler(ctx, {"query": "x", "knowledge_bases": [f"{source.id}.ds1"]})
    with pytest.raises(ToolFailure, match="不在本轮选择"):
        await tools["knowledge_search"].handler(ctx, {"query": "x", "knowledge_bases": [f"{source.id}.*"]})
    with pytest.raises(ToolFailure, match="不在本轮选择"):
        await tools["knowledge_read"].handler(ctx, {"source_id": source.id, "doc_ref": "ds1/contract"})
    hits = json.loads(await tools["knowledge_search"].handler(ctx, {"query": "x"}))
    assert {r["knowledge_base"] for r in hits["results"]} == {f"{source.id}.ds2"}, "没点名就用本轮选择"
    assert any("ks_other" in note for note in hits["notes"])


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
    assert {r["doc_ref"] for r in json.loads(result.content)["results"]} == {"ds1/contract"}


async def test_discard_removes_the_whole_catalog(env):
    evidences = [Evidence(source_id="s", doc_ref="d", span_ref=f"p{i}", title="t", text=str(i)) for i in range(2)]
    await env.knowledge.citations.register("ws1", "turn-gone", evidences)
    await env.knowledge.citations.discard("ws1", "turn-gone")
    assert (await env.knowledge.citations.load("ws1", "turn-gone")).entries == ()
