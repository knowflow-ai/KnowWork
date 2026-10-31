"""IM 里回答过程的一行说明：MCP 工具写成「服务名 · 工具名」，企业 MCP 的 ent_ 前缀是内部的，不给人看。"""

from knowwork_server.channels.progress import tool_line


def test_mcp_tools_show_the_service_and_tool_without_the_enterprise_prefix():
    assert tool_line("mcp__crm__lookup", {}) == "crm · lookup"
    assert tool_line("mcp__ent_gitlab__search", {}) == "gitlab · search"
    assert tool_line("something_else", {}) == "调用工具"
