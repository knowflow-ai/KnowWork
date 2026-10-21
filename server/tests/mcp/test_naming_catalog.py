"""工具命名（与官方 dsh-mcp-client 逐字一致）、默认风险等级、工具清单快照的合并与校验。"""

import pytest

from knowwork_server.tools.mcp import catalog
from knowwork_server.tools.mcp.client import RemoteTool
from knowwork_server.tools.mcp.naming import annotated_risk, public_tool_name, server_tool_pattern
from knowwork_server.tools.mcp.registry import spec_of

# 标准答案由官方 dsh-mcp-client 0.2.1-rc.2 的 publicToolName 原样执行得出
GOLDEN = [
    ("github", "create_issue", "mcp__github__create_issue"),
    ("demo", "kb.search", "mcp__demo__kb_search_431baf9c58bf"),
    ("demo", "kb_search", "mcp__demo__kb_search"),
    ("demo", "x" * 80, "mcp__demo__" + "x" * 40 + "_4fe4e0179bcc"),
    ("jira", "查询工单", "mcp__jira_______42d10abc969c"),
]
OBJECT = {"type": "object", "properties": {"q": {"type": "string"}}}


@pytest.mark.parametrize(("server", "raw", "expected"), GOLDEN)
def test_public_name_matches_official_client(server, raw, expected):
    name = public_tool_name(server, raw)
    assert name == expected
    assert len(name) <= 64 and name.startswith(f"mcp__{server}__")


def test_server_pattern_covers_hashed_names_too():
    prefix = server_tool_pattern("demo")[:-1]
    assert all(public_tool_name("demo", raw).startswith(prefix) for raw in ("a", "kb.search", "y" * 90))


@pytest.mark.parametrize(("annotations", "risk"), [
    ({"readOnlyHint": True}, "read"),
    ({"readOnlyHint": False, "destructiveHint": True}, "dangerous"),
    ({"readOnlyHint": False}, "write"),
    (None, "write"),
])
def test_annotated_risk(annotations, risk):
    assert annotated_risk(annotations) == risk


def remote(name: str, schema=OBJECT, annotations=None, description="") -> RemoteTool:
    return RemoteTool(name=name, description=description, input_schema=schema, annotations=annotations)


def test_build_validates_schema_and_keeps_admin_settings():
    first = catalog.build("demo", [remote("a", annotations={"readOnlyHint": True}), remote("b")], [])
    assert [(t.name, t.risk, t.usable) for t in first] == [("mcp__demo__a", "read", True),
                                                           ("mcp__demo__b", "write", True)]
    assert first[0].description == "MCP 工具 a", "没有说明时给一个兜底说明"
    tuned = catalog.configure(first, "b", risk="dangerous", approval="never", enabled=False)
    again = catalog.build("demo", [remote("b"), remote("c", schema={"type": "array"}),
                                   remote("d", schema={"type": "object", "properties": 3})], tuned)
    b, c, d = again
    assert (b.risk, b.approval_override, b.enabled) == ("dangerous", "never", False), "按原名保留管理员的设置"
    assert c.problem == "参数结构的根不是对象" and not c.usable
    assert d.problem == "参数结构不是合法的 JSON Schema"
    assert [t.raw_name for t in again] == ["b", "c", "d"], "服务端去掉的工具随之消失"


def test_configure_auto_restores_derived_values():
    tools = catalog.build("demo", [remote("a", annotations={"readOnlyHint": True})], [])
    tuned = catalog.configure(tools, "a", risk="write", approval="ask")
    assert spec_of(tuned[0]).approval_mode == "ask" and tuned[0].risk == "write"
    restored = catalog.configure(tuned, "a", risk="auto", approval="auto")
    assert (restored[0].risk, spec_of(restored[0]).approval_mode) == ("read", "never")
    with pytest.raises(KeyError):
        catalog.configure(tools, "missing", enabled=False)


def test_snapshot_round_trips_through_json():
    tools = catalog.configure(catalog.build("demo", [remote("a", description="说明")], []), "a", risk="read")
    assert catalog.load(catalog.dump(tools)) == tools
