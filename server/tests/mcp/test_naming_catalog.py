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
    first = catalog.build("demo", [remote("a", annotations={"readOnlyHint": True}), remote("b")], [], first=True)
    assert [(t.name, t.group, t.policy, t.usable) for t in first] == [
        ("mcp__demo__a", "read", "allow", True), ("mcp__demo__b", "write", "ask", True)], "第一次登记照注解预填"
    assert first[0].description == "MCP 工具 a", "没有说明时给一个兜底说明"
    tuned = catalog.set_policy(first, "b", policy="block")
    again = catalog.build("demo", [remote("b"), remote("c", schema={"type": "array"}),
                                   remote("d", schema={"type": "object", "properties": 3})], tuned)
    b, c, d = again
    assert b.policy == "block", "按原名保留管理员的设置"
    assert c.problem == "参数结构的根不是对象" and not c.usable
    assert d.problem == "参数结构不是合法的 JSON Schema"
    assert [t.raw_name for t in again] == ["b", "c", "d"], "服务端去掉的工具随之消失"


def test_new_tools_after_first_sync_need_confirmation_and_are_marked():
    first = catalog.build("demo", [remote("a")], [], first=True)
    again = catalog.build("demo", [remote("a"), remote("e", annotations={"readOnlyHint": True})], first)
    e = again[1]
    assert (e.group, e.policy, e.is_new) == ("read", "ask", True), "对方标了只读也不直接放开"
    assert spec_of(e).approval_mode == "ask"
    seen = catalog.set_policy(again, "e", policy="allow")
    assert (seen[1].policy, seen[1].is_new) == ("allow", False), "管理员改过就不再算新"
    assert catalog.build("demo", [remote("a"), remote("e")], seen)[1].is_new is False


def test_policy_and_group_map_onto_approval_and_risk():
    tools = catalog.build("demo", [remote("a", annotations={"readOnlyHint": True}),
                                   remote("x", annotations={"destructiveHint": True})], [], first=True)
    assert [(t.group, t.policy) for t in tools] == [("read", "allow"), ("write", "ask")], "高风险并进写入组"
    moved = catalog.set_policy(tools, "a", group="write")
    assert (moved[0].risk, moved[0].policy) == ("write", "allow"), "挪组不改三档"
    asked = catalog.set_policy(moved, "a", policy="ask")
    assert spec_of(asked[0]).approval_mode == "ask" and asked[0].enabled
    back = catalog.set_policy(asked, "a", group="read")
    assert (back[0].risk_override, back[0].group) == (None, "read"), "挪回注解的分组就不再覆盖"
    allowed = catalog.set_policy(tools, "x", policy="allow")
    assert spec_of(allowed[1]).approval_mode == "never", "写入工具也可以设成允许"
    with pytest.raises(KeyError):
        catalog.set_policy(tools, "missing", policy="block")
    with pytest.raises(ValueError):
        catalog.set_policy(tools, "a", policy="maybe")


def test_snapshot_round_trips_through_json():
    built = catalog.build("demo", [remote("a", description="说明")], [], first=True)
    tools = catalog.build("demo", [remote("a"), remote("b")], catalog.set_policy(built, "a", group="read"))
    assert tools[1].is_new and catalog.load(catalog.dump(tools)) == tools
