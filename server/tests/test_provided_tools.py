"""扩展提供的工具（tools/provided.py）：按会话判断给不给，列工具与执行前都问；和内置工具一样只要求是空间成员；
重名时内置工具优先；判断出错时不给，不拖垮整张清单。"""

import pytest

from knowwork_server.profile.clearance import AuthorizedToolRegistry, ToolClearance
from knowwork_server.tools.provided import ProvidedTool, ProvidedTools
from knowwork_server.tools.registry import (
    KW_NOW,
    BuiltinTool,
    CompositeToolRegistry,
    StaticToolRegistry,
    ToolContext,
    ToolFailure,
    ToolSpec,
)

pytestmark = pytest.mark.anyio
SCHEMA = {"type": "object", "properties": {"content": {"type": "string"}}}
ALICE = ToolContext("ws1", "c1", "alice")
CHANNEL = ToolContext("ws1", "c2", "ch_bot")


def tool(name: str, reply: str = "记下了") -> BuiltinTool:
    async def handler(ctx, arguments):
        return f"{reply}：{arguments.get('content', '')}"

    return BuiltinTool(ToolSpec(name, "记一条", SCHEMA, "write", approval="never"), handler)


async def people_only(ctx: ToolContext) -> bool:
    return ctx.user_id != "ch_bot"


class Members:
    """alice、ch_bot 是空间成员，没有任何 tool 授权；bob 不是成员。"""

    def check(self, user_id, workspace_id, resource, action):
        return resource == "workspace" and user_id in {"alice", "ch_bot"}


def stack(provided: ProvidedTools) -> AuthorizedToolRegistry:
    clearance = ToolClearance(Members(), builtin=frozenset({KW_NOW.spec.name}), provided=provided)
    return AuthorizedToolRegistry(CompositeToolRegistry(StaticToolRegistry.of([KW_NOW]), provided), clearance)


async def test_a_provided_tool_is_offered_per_session_and_checked_again_before_running():
    provided = ProvidedTools()
    provided.add(ProvidedTool(tool("memory_note"), available=people_only))
    registry = stack(provided)
    assert [s.name for s in await registry.available(ALICE)] == ["kw_now", "memory_note"], "成员不用 tool 授权就能用"
    assert [s.name for s in await registry.available(CHANNEL)] == ["kw_now"], "扩展说不给的会话看不到"
    assert await registry.invoke(ALICE, "memory_note", {"content": "用表格"}) == "记下了：用表格"
    with pytest.raises(ToolFailure):
        await registry.invoke(CHANNEL, "memory_note", {"content": "越权"})
    assert await registry.available(ToolContext("ws1", "c3", "bob")) == [], "不是空间成员什么都没有"
    assert stack(provided)._clearance.builtin_names() == {"kw_now", "memory_note"}


async def test_builtin_tools_win_name_clashes_and_a_broken_check_hides_only_that_tool():
    async def broken(ctx):
        raise RuntimeError("扩展挂了")

    provided = ProvidedTools()
    provided.add(ProvidedTool(tool("kw_now", reply="冒名")))
    provided.add(ProvidedTool(tool("memory_broken"), available=broken))
    provided.add(ProvidedTool(tool("memory_fine")))
    registry = stack(provided)
    assert [s.name for s in await registry.available(ALICE)] == ["kw_now", "memory_fine"]
    assert "冒名" not in await registry.invoke(ALICE, "kw_now", {})


def test_names_must_be_plain_and_unique():
    provided = ProvidedTools()
    provided.add(ProvidedTool(tool("memory_note")))
    for bad in ("memory_note", "mcp__x__y", "Memory", "m"):
        with pytest.raises(ValueError):
            provided.add(ProvidedTool(tool(bad)))
