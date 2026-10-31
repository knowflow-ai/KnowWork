"""MCP 服务的登记、修改、同步与工具设置。

- 全部操作要 workspace:edit（空间 owner、admin），与新建知识源同一口径；成员用不用得上某个工具由 tool 授权决定：
  新登记的服务在同一个事务里默认授全体成员（role:editor 对 tool:mcp__<服务名>__* 的 use），在服务的设置里改「给谁用」；
- 每个工具一个三档（允许、需确认、禁用），见 catalog.py；
- 认证方式：共享凭据用管理员填的请求头；个人访问令牌时管理员也要填自己的令牌，只用来测试连接、同步工具清单，
  存成他自己的连接（connections.py），不用于别人的调用；之后刷新、改地址用操作的管理员自己的连接；
  OAuth 授权时登记只做发现与注册（oauth_login.prepare），登记人随后登录一次，回调里用他的令牌拉第一份工具清单；
- 认证方式、OAuth 应用或地址（OAuth 授权时）变了，原来的连接全部作废，成员按新的方式重新连接；
- 新建、改地址或请求头时先连一次、拉工具清单，连不上就什么都不改（Unreachable），不存半成品；
- 手动刷新连不上时保留原来的工具清单，只记下失败原因，正在用的会话不受影响；
- 停用服务或工具立即生效：ToolService 执行前会按注册表此刻的判定重查。

企业 MCP（enterprise.py）沿用这一套，只换四处：范围（SCOPE）、谁能管（_require）、存的名字（_stored_name，加 ent_ 前缀）、
新建时默认给谁用（_seed，企业服务到了空间默认全体成员，不靠授权）。按 id 取服务时核对范围（_current），
默认空间的管理员碰不到存在同一个空间名下的企业服务。
"""

import logging
from dataclasses import replace
from datetime import UTC, datetime

from ...access.workspaces import PLATFORM_WORKSPACE_ID
from . import catalog
from .catalog import CatalogTool
from .client import Endpoint, McpClient, McpUnavailable
from .connections import McpConnections
from .errors import Forbidden, InvalidInput, NotFound, Unreachable
from .naming import PREFIX, server_tool_pattern
from .oauth import OAuthClient, OAuthFailure
from .oauth_login import McpOAuthLogin, Probe
from .schemas import McpServerCreate, McpServerPatch, McpToolPatch, OAuthInput, TokenConfig
from .store import McpChanges, McpServerRecord, McpServerStore, new_server_id

log = logging.getLogger(__name__)

WORKSPACE = "workspace"
EVERYONE = "role:editor"  # 「全体成员」：成员角色；所有者、管理员对全部工具本来就有权限


class McpAdmin:
    SCOPE = "workspace"

    def __init__(self, store: McpServerStore, client: McpClient, access, connections: McpConnections,
                 login: McpOAuthLogin) -> None:
        self._store = store
        self._client = client
        self._access = access
        self._connections = connections
        self._login = login

    @property
    def redirect_uri(self) -> str:
        """OAuth 授权的回调地址：对方不支持自动注册时，管理员要把它填到对方的应用里。"""
        return self._login.redirect_uri

    async def probe(self, actor: str, workspace_id: str, url: str) -> Probe:
        self._require(actor, workspace_id)
        return await self._login.probe(url)

    async def list_servers(self, actor: str, workspace_id: str) -> list[McpServerRecord]:
        self._require(actor, workspace_id)
        return await self._store.list_servers(workspace_id, self.SCOPE)

    async def get(self, actor: str, workspace_id: str, server_id: str) -> McpServerRecord:
        self._require(actor, workspace_id)
        return await self._current(workspace_id, server_id)

    async def promoted(self, workspace_id: str) -> dict[str, str]:
        """本空间里已经收为企业 MCP 的服务：服务 id → 企业 MCP 的名称（页面提醒确认企业 MCP 能用后可以删掉这份）。"""
        enterprise = await self._store.list_servers(PLATFORM_WORKSPACE_ID, "enterprise")
        own = {server.id for server in await self._store.list_servers(workspace_id)}
        return {server.origin_server_id: server.title for server in enterprise if server.origin_server_id in own}

    async def _current(self, workspace_id: str, server_id: str) -> McpServerRecord:
        """按 id 取服务并核对范围：企业服务和默认空间自己的服务存在同一个空间名下，不能互相碰到。"""
        record = await self._store.get(workspace_id, server_id)
        if record.scope != self.SCOPE:
            raise NotFound()
        return record

    def _stored_name(self, name: str) -> str:
        """存进库里的名字（工具名前缀 mcp__<名字>__ 用它）。"""
        return name

    async def _seed(self, session, workspace_id: str, name: str):
        """新建时默认给谁用：空间服务授全体成员（同一个事务）。返回要交给 settle 的授权变化，没有就是 None。"""
        return await self._access.seed_users(session, workspace_id, f"tool:{server_tool_pattern(name)}", (EVERYONE,))

    async def create(self, actor: str, workspace_id: str, body: McpServerCreate, *,
                     template: McpServerRecord | None = None) -> McpServerRecord:
        """template：照着另一个服务建（收为企业 MCP），它每个工具的三档、分组按原名带过来；凭据不带，用 body 里的。"""
        self._require(actor, workspace_id)
        previous = list(template.tools) if template is not None else []
        name = self._stored_name(body.name)
        per_user = body.auth == "user_token"
        config = body.token_config or TokenConfig()
        if per_user and body.token is None:
            raise InvalidInput("个人访问令牌方式，登记时要填你自己的令牌，用来测试连接、拉工具清单")
        oauth_client: OAuthClient | None = None
        if body.auth == "oauth":
            oauth = body.oauth or OAuthInput()
            prepared = await self._login.prepare(body.url, client_id=oauth.client_id,
                                                 client_secret=oauth.client_secret, scope=oauth.scope)
            # 还拉不到清单（要等登记人登录）：先放来源的工具与设置，登录后拉第一份清单时按原名合并
            auth_config, oauth_client, synced_at = prepared.auth_config, prepared.client, None
            tools = catalog.adopt(name, previous)
        else:
            headers = config.headers(body.token) if per_user and body.token else body.headers
            endpoint = Endpoint(url=body.url, headers=headers, timeout_seconds=body.timeout_seconds)
            tools = await self._fetch(name, endpoint, previous, first=True)
            auth_config, synced_at = (config.model_dump() if per_user else None), _now()
        server_id = new_server_id()

        async def also(session, stored: str):
            if per_user and body.token:  # 登记人自己的连接，同一个事务里存
                await self._connections.save(workspace_id, server_id, actor, body.token, session=session)
            return await self._seed(session, workspace_id, stored)

        record, change = await self._store.create(
            workspace_id, name=name, title=body.title, url=body.url,
            headers=body.headers if body.auth == "shared" else {}, timeout_seconds=body.timeout_seconds, tools=tools,
            synced_at=synced_at, created_by=actor, also=also, auth=body.auth, auth_config=auth_config,
            oauth_client=oauth_client.dump() if oauth_client else None, server_id=server_id, scope=self.SCOPE,
            origin_server_id=template.id if template is not None else None)
        if change is not None:
            await self._access.settle(change)
        return record

    async def update(self, actor: str, workspace_id: str, server_id: str, body: McpServerPatch) -> McpServerRecord:
        self._require(actor, workspace_id)
        current = await self._current(workspace_id, server_id)
        auth = body.auth or current.auth
        changes = McpChanges(title=body.title, url=body.url, timeout_seconds=body.timeout_seconds, enabled=body.enabled,
                             auth=body.auth)
        if auth == "oauth":
            changes, relogin = await self._login_changes(current, body, changes)
        else:
            changes = await self._credential_changes(actor, current, body, changes)
            relogin = auth != current.auth
        if relogin:  # 认证方式或 OAuth 应用变了：原来的连接作废
            await self._connections.forget_server(workspace_id, server_id)
        if auth == "user_token" and body.token:
            await self._connections.save(workspace_id, server_id, actor, body.token)
        return await self._store.update(workspace_id, server_id, changes)

    async def _credential_changes(self, actor: str, current: McpServerRecord, body: McpServerPatch,
                                  changes: McpChanges) -> McpChanges:
        """共享凭据、个人访问令牌：改了地址、请求头、认证方式、令牌或令牌设置时先连一次，拉新的工具清单。"""
        auth = body.auth or current.auth
        per_user = auth == "user_token"
        switched = auth != current.auth
        config = body.token_config or (TokenConfig(**current.auth_config)
                                       if current.auth == "user_token" and current.auth_config else TokenConfig())
        changes = replace(changes, headers={} if per_user and switched else body.headers,
                          auth_config=config.model_dump() if per_user and (body.token_config is not None or switched)
                          else None, clear_oauth_client=current.auth == "oauth")
        reconnect = body.url is not None or body.headers is not None or switched or body.token is not None \
            or body.token_config is not None
        if not reconnect:
            return changes
        if per_user:
            token = body.token or (None if switched else await self._connections.token(current.workspace_id,
                                                                                         current.id, actor))
            if token is None:
                raise InvalidInput("个人访问令牌方式，要填你自己的令牌来测试连接")
            headers = config.headers(token)
        else:
            headers = self._store.headers(current) if body.headers is None else body.headers
        endpoint = Endpoint(url=body.url or current.url, headers=headers,
                            timeout_seconds=body.timeout_seconds or current.timeout_seconds)
        tools = await self._fetch(current.name, endpoint, list(current.tools))
        return replace(changes, tools=tools, synced_at=_now(), clear_sync_error=True)

    async def _login_changes(self, current: McpServerRecord, body: McpServerPatch,
                             changes: McpChanges) -> tuple[McpChanges, bool]:
        """OAuth 授权：改成 OAuth 授权、换了地址或 OAuth 应用时重新发现与注册（连接作废）；只改 scope 时连接保留，下次登录生效。
        工具清单不动：要等管理员重新登录后再刷新。"""
        was_oauth = current.auth == "oauth"
        config = dict(current.auth_config or {}) if was_oauth else {}
        current_manual = None if config.get("dynamic") else config.get("client_id")
        oauth = body.oauth
        manual = (oauth.client_id if oauth is not None else current_manual) or None
        new_secret = oauth.client_secret if oauth is not None else None
        url_changed = body.url is not None and body.url != current.url
        client_changed = was_oauth and (manual != current_manual or new_secret is not None)
        changes = replace(changes, headers={} if not was_oauth else None)
        if was_oauth and not url_changed and not client_changed:
            if oauth is not None:
                changes = replace(changes, auth_config={**config, "scope": oauth.scope})
            return changes, False
        secret = new_secret
        if secret is None and manual is not None and manual == current_manual:
            secret = OAuthClient.load(self._store.oauth_client(current) or "{}").client_secret  # 沿用原来的
        scope = oauth.scope if oauth is not None else config.get("scope")
        prepared = await self._login.prepare(body.url or current.url, client_id=manual, client_secret=secret,
                                             scope=scope)
        return replace(changes, auth_config=prepared.auth_config, oauth_client=prepared.client.dump()), True

    async def sync(self, actor: str, workspace_id: str, server_id: str) -> McpServerRecord:
        self._require(actor, workspace_id)
        current = await self._current(workspace_id, server_id)
        endpoint = Endpoint(url=current.url, headers=await self._own_headers(actor, current),
                            timeout_seconds=current.timeout_seconds)
        try:
            tools = await self._fetch(current.name, endpoint, list(current.tools), first=current.synced_at is None)
        except Unreachable as error:
            await self._store.update(workspace_id, server_id, McpChanges(sync_error=str(error)))
            raise
        return await self._store.update(workspace_id, server_id,
                                        McpChanges(tools=tools, synced_at=_now(), clear_sync_error=True))

    async def configure_tool(self, actor: str, workspace_id: str, server_id: str, tool_name: str,
                             body: McpToolPatch) -> McpServerRecord:
        self._require(actor, workspace_id)
        current = await self._current(workspace_id, server_id)
        target = next((tool for tool in current.tools if tool.name == tool_name), None)
        if target is None:
            raise NotFound("这个 MCP 服务没有该工具")
        tools = catalog.set_policy(list(current.tools), target.raw_name, policy=body.policy, group=body.group)
        return await self._store.update(workspace_id, server_id, McpChanges(tools=tools))

    async def delete(self, actor: str, workspace_id: str, server_id: str) -> None:
        """连同它的工具授权一起删（同一事务）：单个工具的 tool:mcp__<服务名>__<工具> 与整个服务的 …__*。"""
        self._require(actor, workspace_id)
        await self._current(workspace_id, server_id)
        change = await self._store.delete(workspace_id, server_id, also=lambda session, name: self._access.purge_grants(
            session, workspace_id, prefixes=(f"tool:{PREFIX}{name}__",)))
        await self._access.settle(change)

    async def _own_headers(self, actor: str, server: McpServerRecord) -> dict[str, str]:
        """刷新时用的凭据：共享凭据用那份请求头；每人方式用操作的管理员自己的连接。"""
        if not server.per_user:
            return self._store.headers(server)
        if server.auth == "oauth":
            try:
                token = await self._login.bearer(server.workspace_id, server, actor)
            except OAuthFailure as error:
                raise Unreachable(str(error)) from error
            if token is None:
                raise Unreachable(f"这个服务用 OAuth 授权：先授权你自己的 {server.title} 账号，再刷新")
            return {"Authorization": f"Bearer {token}"}
        token = await self._connections.token(server.workspace_id, server.id, actor)
        if token is None:
            raise Unreachable(f"这个服务用个人访问令牌：先在「设置 → 连接」里连接你自己的 {server.title} 账号，再刷新")
        return TokenConfig(**(server.auth_config or {})).headers(token)

    async def _fetch(self, server_name: str, endpoint: Endpoint, previous: list[CatalogTool], *,
                     first: bool = False) -> list[CatalogTool]:
        try:
            remote = await self._client.list_tools(endpoint)
        except McpUnavailable as error:
            raise Unreachable(str(error)) from error
        return catalog.build(server_name, remote, previous, first=first)

    def _require(self, actor: str, workspace_id: str) -> None:
        try:
            allowed = self._access.check(actor, workspace_id, WORKSPACE, "edit") is True
        except Exception:
            log.exception("mcp admin permission check failed; denying")
            allowed = False
        if not allowed:
            raise Forbidden()


def _now() -> datetime:
    return datetime.now(UTC)

