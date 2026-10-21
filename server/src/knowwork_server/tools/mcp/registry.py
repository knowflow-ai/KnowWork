"""MCP 工具接到 ToolRegistry 接口后面：上层（ToolService、工具桥、审批、动作回执、审计）不用改。

- available：读本空间已启用服务的工具清单快照，只给启用且参数结构合格的工具，不访问网络；
- invoke：按对外名字找到服务与原名，解开请求头，调一次 MCP 服务。MCP 返回 isError 或连不上时抛
  ToolFailure，文案交给模型；
- 权限不在这里判：外层 AuthorizedToolRegistry 按 `tool:<名字>` 过滤（默认只有 owner、admin 能用）。
"""

from typing import Any

from ..registry import ToolContext, ToolFailure, ToolSpec
from .catalog import CatalogTool
from .client import Endpoint, McpClient, McpUnavailable
from .store import McpServerRecord, McpServerStore


def spec_of(tool: CatalogTool) -> ToolSpec:
    return ToolSpec(name=tool.name, description=tool.description, input_schema=tool.input_schema, risk=tool.risk,
                    approval=tool.approval_override)


class McpToolRegistry:
    def __init__(self, store: McpServerStore, client: McpClient) -> None:
        self._store = store
        self._client = client

    async def available(self, ctx: ToolContext) -> list[ToolSpec]:
        return [spec_of(tool) for server in await self._servers(ctx) for tool in server.tools if tool.usable]

    async def invoke(self, ctx: ToolContext, name: str, arguments: dict[str, Any]) -> str:
        found = next(((server, tool) for server in await self._servers(ctx) for tool in server.tools
                      if tool.name == name and tool.usable), None)
        if found is None:
            raise ToolFailure(f"工具 {name} 不存在或已停用")
        server, tool = found
        endpoint = Endpoint(url=server.url, headers=self._store.headers(server),
                            timeout_seconds=server.timeout_seconds)
        try:
            outcome = await self._client.call_tool(endpoint, tool.raw_name, arguments)
        except McpUnavailable as error:
            raise ToolFailure(f"{server.title}：{error}") from error
        if outcome.is_error:
            raise ToolFailure(outcome.text or f"工具 {name} 执行出错")
        return outcome.text

    async def _servers(self, ctx: ToolContext) -> list[McpServerRecord]:
        return [server for server in await self._store.list_servers(ctx.workspace_id) if server.enabled]
