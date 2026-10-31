"""MCP 工具接到 ToolRegistry 接口后面：上层（ToolService、工具桥、审批、动作回执、审计）不用改。

- available：读这个人在本空间看得到的已启用服务（本空间的，加上到达本空间、没停用、收窄后他在内的企业服务，见
  enterprise.McpReach）的工具清单快照，只给没禁用且参数结构合格的工具，不访问网络；每人凭据的服务，
  本人还没连接的不给（工具声明在会话开始时就定了，没凭据给了模型也调不成）；连接失效的照样给，调用时告诉他去重连；
- invoke：按对外名字找到服务与原名，带上凭据调一次 MCP 服务：共享凭据用管理员填的请求头，每人凭据用调用者自己的
  （OAuth 授权的访问令牌快过期时先刷新，对方拒绝刷新令牌也算连接失效）；
  对方拒绝了某人的凭据（401、403）就把他的连接标成失效，不回落到共用凭据。MCP 返回 isError 或连不上时抛
  ToolFailure，文案交给模型；
- 空间服务的权限不在这里判：外层 AuthorizedToolRegistry 按 `tool:<名字>` 过滤；企业服务的在 McpReach 里判；
- 每人的连接按服务所在的空间读写（企业服务在默认空间名下，一个人一份）。
"""

from typing import Any

from ..registry import ToolContext, ToolFailure, ToolSpec
from .catalog import CatalogTool
from .client import Endpoint, McpClient, McpUnavailable
from .connections import McpConnections
from .enterprise import McpReach
from .oauth import OAuthFailure
from .oauth_login import McpOAuthLogin
from .schemas import TokenConfig
from .store import McpServerRecord, McpServerStore


def spec_of(tool: CatalogTool) -> ToolSpec:
    return ToolSpec(name=tool.name, description=tool.description, input_schema=tool.input_schema, risk=tool.risk,
                    approval=tool.approval_override)


class McpToolRegistry:
    def __init__(self, store: McpServerStore, client: McpClient, connections: McpConnections,
                 login: McpOAuthLogin, reach: McpReach) -> None:
        self._store = store
        self._client = client
        self._connections = connections
        self._login = login
        self._reach = reach

    async def available(self, ctx: ToolContext) -> list[ToolSpec]:
        servers = await self._servers(ctx)
        connected = await self._connected(ctx, servers)
        return [spec_of(tool) for server in servers if not server.per_user or server.id in connected
                for tool in server.tools if tool.usable]

    async def invoke(self, ctx: ToolContext, name: str, arguments: dict[str, Any]) -> str:
        found = next(((server, tool) for server in await self._servers(ctx) for tool in server.tools
                      if tool.name == name and tool.usable), None)
        if found is None:
            raise ToolFailure(f"工具 {name} 不存在或已停用")
        server, tool = found
        headers = await self._headers(ctx, server)
        try:
            outcome = await self._call(server, tool.raw_name, headers, arguments)
        except McpUnavailable as error:
            fresh = await self._renewed(ctx, server, headers) if server.auth == "oauth" and error.status == 401 \
                else None
            if fresh is None:
                raise await self._failure(ctx, server, error) from error
            try:
                outcome = await self._call(server, tool.raw_name, fresh, arguments)
            except McpUnavailable as again:
                raise await self._failure(ctx, server, again) from again
        if outcome.is_error:
            raise ToolFailure(outcome.text or f"工具 {name} 执行出错")
        return outcome.text

    async def _call(self, server: McpServerRecord, raw_name: str, headers: dict[str, str],
                    arguments: dict[str, Any]):
        endpoint = Endpoint(url=server.url, headers=headers, timeout_seconds=server.timeout_seconds)
        return await self._client.call_tool(endpoint, raw_name, arguments)

    async def _renewed(self, ctx: ToolContext, server: McpServerRecord,
                       used: dict[str, str]) -> dict[str, str] | None:
        """OAuth 的访问令牌被拒（401）：可能是对方没给过期时间、或提前作废了它，用刷新令牌换一张再试一次。
        401 是对方在执行工具之前拒绝的，重试不会让工具做两遍。换不到（刷新令牌也被拒、连不上）返回 None。"""
        stale = used.get("Authorization", "").removeprefix("Bearer ")
        try:
            token = await self._login.bearer(server.workspace_id, server, ctx.user_id, stale=stale)
        except OAuthFailure:
            return None
        return {"Authorization": f"Bearer {token}"} if token and token != stale else None

    async def _failure(self, ctx: ToolContext, server: McpServerRecord, error: McpUnavailable) -> ToolFailure:
        """对方拒绝了这个人的凭据：标成失效，不回落到共享凭据；其余照原因报。"""
        if server.per_user and error.rejected:
            await self._connections.mark_invalid(server.workspace_id, server.id, ctx.user_id)
            return ToolFailure(f"你的 {server.title} 连接失效了（对方拒绝了这份凭据），请到「设置 → 连接」里重新连接")
        return ToolFailure(f"{server.title}：{error}")

    async def _headers(self, ctx: ToolContext, server: McpServerRecord) -> dict[str, str]:
        if not server.per_user:
            return self._store.headers(server)
        if server.auth == "oauth":
            try:
                token = await self._login.bearer(server.workspace_id, server, ctx.user_id)
            except OAuthFailure as error:
                raise ToolFailure(f"{server.title}：暂时没能续上你的授权（{error}），请稍后再试") from error
        else:
            token = await self._connections.token(server.workspace_id, server.id, ctx.user_id)
        if token is None:
            raise ToolFailure(f"你的 {server.title} 还没连接或连接已失效，请到「设置 → 连接」里连接你自己的账号")
        if server.auth == "oauth":
            return {"Authorization": f"Bearer {token}"}
        return TokenConfig(**(server.auth_config or {})).headers(token)

    async def _connected(self, ctx: ToolContext, servers: list[McpServerRecord]) -> set[str]:
        """连接过的服务（含已失效的：照样给工具，调用时提示重连）。"""
        return set(await self._connections.for_servers(servers, ctx.user_id))

    async def _servers(self, ctx: ToolContext) -> list[McpServerRecord]:
        return [server for server in await self._reach.servers(ctx.workspace_id, ctx.user_id) if server.enabled]
