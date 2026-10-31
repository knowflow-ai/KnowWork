"""成员自己的 MCP 连接（「设置 → 连接」）：列出本人能用的全部服务与工具（让成员知道能让 AI 做什么），
要用自己凭据的在这里连接、断开：

- 个人访问令牌：填令牌，保存前先用它连一次；
- OAuth 授权：login 返回对方的授权地址，浏览器跳过去登录，回调在 oauth_login.complete 里存成连接。

能用 = 本空间的或到达本空间的企业服务（enterprise.McpReach）、服务启用、本人对它至少一个没禁用的工具有使用权限
（给谁用、资源授权）。别的服务当作不存在。连接按服务所在的空间读写：企业服务的连接一个人一份，换空间不用重连。
无需认证、共享凭据的服务状态是「可以直接用」（ready），不用连接。
管理员刚登记完的 OAuth 授权服务还没有工具清单，成员看不到；管理员自己可以先登录一次（登录后拉第一份工具清单）。
"""

from collections.abc import Callable

from .catalog import CatalogTool
from .client import Endpoint, McpClient, McpUnavailable
from .connections import McpConnections
from .enterprise import McpReach
from .errors import Forbidden, InvalidInput, NotFound, Unreachable
from .oauth import OAuthFailure
from .oauth_login import McpOAuthLogin
from .schemas import ConnectionTool, ConnectionView, TokenConfig
from .store import McpServerRecord, McpServerStore

MAX_TOOL_DESCRIPTION = 500  # 列表里给人看的说明，过长的截断（有的服务把用法整段写在说明里）

# (用户, 空间, 工具名) → 能不能用（profile.clearance.ToolClearance.permits）
Permits = Callable[[str, str, str], bool]


class McpMe:
    def __init__(self, store: McpServerStore, client: McpClient, connections: McpConnections, access,
                 permits: Permits, login: McpOAuthLogin, reach: McpReach) -> None:
        self._store = store
        self._client = client
        self._connections = connections
        self._access = access
        self._permits = permits
        self._login = login
        self._reach = reach

    def _member(self, user_id: str, workspace_id: str) -> None:
        if not self._access.check(user_id, workspace_id, "workspace", "use"):
            raise Forbidden("不是这个空间的成员")

    async def _usable(self, user_id: str, workspace_id: str) -> list[tuple[McpServerRecord, list[CatalogTool]]]:
        """本人能用的服务，各带本人能用的工具（没禁用、参数结构合格、有使用权限）；一个都没有的服务不算。"""
        found = []
        for server in await self._reach.servers(workspace_id, user_id):
            tools = [tool for tool in server.tools
                     if tool.usable and self._permits(user_id, workspace_id, tool.name)] if server.enabled else []
            if tools:
                found.append((server, tools))
        return found

    async def _servers(self, user_id: str, workspace_id: str) -> list[McpServerRecord]:
        """其中要本人连接的（个人访问令牌、OAuth 授权）。"""
        return [server for server, _ in await self._usable(user_id, workspace_id) if server.per_user]

    # 写在 list 方法前面：类体里 list 方法定义之后，注解里的 list[...] 指的就是这个方法了
    async def mentionable(self, user_id: str, workspace_id: str) -> list[dict]:
        """输入框 @ 菜单里的 MCP 服务：本人能用、这一轮工具在对话里的（不用连接的、已连接的；连接失效的也算，
        调用时会提示重连）。还没连接的不列：它的工具不在对话里，@ 了模型也用不上。"""
        usable = await self._usable(user_id, workspace_id)
        mine = await self._connections.for_servers((server for server, _ in usable), user_id)
        found = []
        for server, tools in usable:
            status = _view(server, tools, mine.get(server.id)).status
            if status != "none":
                found.append({"name": server.name, "title": server.title, "tools": len(tools), "status": status})
        return found

    async def list(self, user_id: str, workspace_id: str) -> list[ConnectionView]:
        self._member(user_id, workspace_id)
        usable = await self._usable(user_id, workspace_id)
        mine = await self._connections.for_servers((server for server, _ in usable), user_id)
        return [_view(server, tools, mine.get(server.id)) for server, tools in usable]

    async def connect(self, user_id: str, workspace_id: str, server_id: str, token: str) -> ConnectionView:
        self._member(user_id, workspace_id)
        server, tools = next(((s, t) for s, t in await self._usable(user_id, workspace_id)
                              if s.id == server_id and s.per_user), (None, []))
        if server is None:
            raise NotFound("没有这个要连接的服务")
        if server.auth != "user_token":
            raise InvalidInput(f"{server.title} 用 OAuth 授权：点「连接」会跳到对方页面授权")
        endpoint = Endpoint(url=server.url, headers=TokenConfig(**(server.auth_config or {})).headers(token),
                            timeout_seconds=server.timeout_seconds)
        try:
            await self._client.list_tools(endpoint)
        except McpUnavailable as error:
            if error.rejected:
                raise InvalidInput(f"{server.title} 拒绝了这份令牌：令牌不对，或这个账号没有权限") from error
            raise Unreachable(str(error)) from error
        await self._connections.save(server.workspace_id, server.id, user_id, token)
        return _view(server, tools, (await self._connections.for_servers([server], user_id)).get(server.id))

    async def login(self, user_id: str, workspace_id: str, server_id: str, return_to: str) -> str:
        """OAuth 授权：返回对方的授权地址。本人能用的服务可以；管理员还可以登录刚登记、还没有工具清单的服务
        （空间服务是空间管理员，企业服务是系统管理员）。授权记在服务所在的空间，回调在那里存连接。"""
        self._member(user_id, workspace_id)
        server = await self._loginable(user_id, workspace_id, server_id)
        if server is None or server.auth != "oauth" or not server.enabled:
            raise NotFound("没有这个要授权的服务")
        try:
            return await self._login.start(user_id, server.workspace_id, server, return_to)
        except OAuthFailure as error:
            raise InvalidInput(str(error)) from error

    async def _loginable(self, user_id: str, workspace_id: str, server_id: str) -> McpServerRecord | None:
        visible = next((s for s in await self._servers(user_id, workspace_id) if s.id == server_id), None)
        if visible is not None:
            return visible
        own = next((s for s in await self._store.list_servers(workspace_id) if s.id == server_id), None)
        if own is not None:
            return own if self._access.check(user_id, workspace_id, "workspace", "edit") else None
        enterprise = await self._reach.enterprise(server_id)
        return enterprise if enterprise is not None and self._access.is_system_admin(user_id) else None

    async def note(self, ctx) -> str | None:
        """随工具清单交给模型的一句话：本人能用、还没连接的服务。工具声明会话开始时就定了，连好后要新开对话。"""
        servers = await self._servers(ctx.user_id, ctx.workspace_id)
        if not servers:
            return None
        mine = await self._connections.for_servers(servers, ctx.user_id)
        missing = [server.title for server in servers if server.id not in mine]
        if not missing:
            return None
        return (f"这些服务要用户先连接自己的账号才能用：{'、'.join(missing)}。用户要做的事用得上它们时，"
                "告诉他到「设置 → 连接」里连接，连好后新开一个对话就能用。")

    async def disconnect(self, user_id: str, workspace_id: str, server_id: str) -> None:
        self._member(user_id, workspace_id)
        enterprise = await self._reach.enterprise(server_id)
        home = enterprise.workspace_id if enterprise is not None else workspace_id
        if not await self._connections.delete(home, server_id, user_id):
            raise NotFound("没有这个连接")


def _view(server: McpServerRecord, tools: list[CatalogTool], connection) -> ConnectionView:
    help_text = str((server.auth_config or {}).get("help", "")) if server.auth == "user_token" else ""
    if not server.per_user:
        status = "ready"
    elif connection is None:
        status = "none"
    else:
        status = "connected" if connection.usable else "invalid"
    return ConnectionView(
        server_id=server.id, title=server.title, auth=server.auth, help=help_text, status=status,
        display=connection.display if connection else "", connected_at=connection.connected_at if connection else None,
        tools=[ConnectionTool(name=tool.raw_name, description=tool.description[:MAX_TOOL_DESCRIPTION])
               for tool in tools])
