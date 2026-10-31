"""OAuth 授权的流程编排：网页里分两次请求走完，协议细节在 oauth.py。

1. 点「连接」（start）：生成 state 与 PKCE，记一条待完成的授权（state 只存摘要，校验串加密存，10 分钟过期），
   返回对方的授权地址，浏览器跳过去；
2. 对方回调 /api/mcp/oauth/callback（complete）：按 state 摘要找到那条记录并立即删掉（只能用一次），要求是发起人本人的
   登录会话，校验 iss，用授权码加校验串换令牌，存成本人的连接。服务还没拉过工具清单（管理员刚登记完第一次登录）时，
   用这份令牌顺手拉一次。

state 写成「空间 id~随机串」：回调时还不知道是哪个空间，要先从 state 里取出空间才能在行级安全下查到记录。
过期的待完成记录在下次发起时顺手清（同 OIDC 登录的做法），不另起后台任务。
"""

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...access.rls import set_workspace
from ...identity.credentials import digest, new_token, same
from ...knowledge.crypto import SecretBox, SecretError
from . import catalog
from .client import Endpoint, McpClient, McpUnavailable
from .connections import McpConnections
from .errors import InvalidInput, Unreachable
from .models import McpOAuthPendingRow
from .oauth import AuthServer, OAuthClient, OAuthFailure, OAuthProtocol, Tokens, manual_client, new_pkce
from .store import McpChanges, McpServerRecord, McpServerStore

PENDING_TTL = timedelta(minutes=10)
MAX_PARAM = 2048
WORKSPACE_ID = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
STATE_SEPARATOR = "~"  # 空间 id 与随机串里都不会出现


@dataclass(frozen=True)
class Probe:
    """登记前探一次地址：不要凭据（例如公开的文档检索）就默认无需认证，即使对方另外支持 OAuth（授权是可选的，
    例如用各自的额度）；要凭据时声明了 OAuth 默认 OAuth 授权，否则默认个人访问令牌。不支持自动注册时要管理员填 client。"""

    oauth: bool
    needs_credentials: bool
    dynamic_registration: bool
    scope: str | None
    redirect_uri: str


@dataclass(frozen=True)
class Prepared:
    """登记或改成 OAuth 授权时准备好的：存进 auth_config 的授权服务器，与要加密存的 client。"""

    auth_config: dict
    client: OAuthClient


class LoginFailed(Exception):
    """回调失败。message 给人看；return_to 是能确定的话回到的页面。"""

    def __init__(self, message: str, return_to: str | None = None) -> None:
        super().__init__(message)
        self.return_to = return_to


@dataclass(frozen=True)
class Pending:
    state_hash: str
    server_id: str
    user_id: str
    verifier_ciphertext: str
    return_to: str
    expires_at: datetime


@dataclass(frozen=True)
class LoginDone:
    server_id: str
    title: str
    return_to: str


def auth_server_of(server: McpServerRecord) -> AuthServer:
    return AuthServer.load((server.auth_config or {})["server"])


def scope_of(server: McpServerRecord) -> str | None:
    """管理员填的 scope 优先，否则用对方建议的。"""
    config = server.auth_config or {}
    return config.get("scope") or auth_server_of(server).scope


def _context(workspace_id: str, state_hash: str) -> str:
    return f"{workspace_id}/mcp/oauth-pending/{state_hash}"


class McpOAuthLogin:
    def __init__(self, sessionmaker: async_sessionmaker, box: SecretBox, store: McpServerStore,
                 connections: McpConnections, client: McpClient, redirect_uri: str,
                 protocol: OAuthProtocol | None = None) -> None:
        self._sessionmaker = sessionmaker
        self._box = box
        self._store = store
        self._connections = connections
        self._client = client
        self._protocol = protocol or OAuthProtocol()
        self.redirect_uri = redirect_uri

    @asynccontextmanager
    async def _session(self, workspace_id: str) -> AsyncIterator[AsyncSession]:
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            yield session

    # ---- 管理员登记 ----

    async def probe(self, url: str) -> Probe:
        try:
            found = await self._protocol.inspect(url)
        except OAuthFailure as error:
            raise Unreachable(str(error)) from error
        server = found.server
        return Probe(oauth=server is not None, needs_credentials=found.challenged,
                     dynamic_registration=server is not None and server.registration_endpoint is not None,
                     scope=server.scope if server else None, redirect_uri=self.redirect_uri)

    async def prepare(self, url: str, *, client_id: str | None, client_secret: str | None,
                      scope: str | None) -> Prepared:
        """发现授权服务器、准备 client：管理员填了 client_id 就用他的，否则动态注册。"""
        try:
            server = await self._protocol.discover(url)
            if server is None:
                raise InvalidInput("这个服务没有声明 OAuth，请改用「个人访问令牌」或「共享凭据」")
            if client_id:
                client = manual_client(server, client_id, client_secret)
            elif server.registration_endpoint is None:
                raise InvalidInput("对方不支持自动注册：请在对方那边建一个应用，回调地址填 "
                                   f"{self.redirect_uri}，再把 Client ID 和 Client Secret 填进来")
            else:
                client = await self._protocol.register(server, self.redirect_uri)
        except OAuthFailure as error:
            raise Unreachable(str(error)) from error
        # client_id 不算密钥，和是不是自动注册、有没有 secret 一起放在 auth_config 里给管理界面看
        return Prepared(auth_config={"server": server.dump(), "scope": scope or None, "client_id": client.client_id,
                                     "dynamic": client.dynamic, "has_secret": bool(client.client_secret)},
                        client=client)

    # ---- 成员登录 ----

    async def start(self, user_id: str, workspace_id: str, server: McpServerRecord, return_to: str) -> str:
        """记一条待完成的授权，返回对方的授权地址。OAuth 应用没配好时抛 OAuthFailure，什么都不记。"""
        auth_server, client = auth_server_of(server), self._oauth_client(server)
        pkce = new_pkce()
        state = f"{workspace_id}{STATE_SEPARATOR}{new_token()}"
        state_hash = digest(state)
        now = datetime.now(UTC)
        async with self._session(workspace_id) as session:
            await session.execute(delete(McpOAuthPendingRow).where(McpOAuthPendingRow.expires_at <= now))
            session.add(McpOAuthPendingRow(
                state_hash=state_hash, workspace_id=workspace_id, server_id=server.id, user_id=user_id,
                verifier_ciphertext=self._box.seal(pkce.code_verifier, context=_context(workspace_id, state_hash)),
                return_to=return_to, expires_at=now + PENDING_TTL))
        return self._protocol.authorize_url(auth_server, client, redirect_uri=self.redirect_uri, state=state,
                                            challenge=pkce.code_challenge, scope=scope_of(server))

    async def complete(self, user_id: str, *, state: str, code: str, iss: str | None, error: str | None) -> LoginDone:
        workspace_id, separator, _ = state.partition(STATE_SEPARATOR)
        if not separator or len(state) > MAX_PARAM or not WORKSPACE_ID.fullmatch(workspace_id):
            raise LoginFailed("授权状态不对，请回到「设置 → 连接」重新连接")
        pending = await self._consume(workspace_id, state)
        if not same(pending.user_id, user_id):
            raise LoginFailed("这次授权不是你发起的，请自己在「设置 → 连接」里连接")
        server = await self._store.get(workspace_id, pending.server_id)
        if error:
            reason = "你在对方页面取消了授权" if error == "access_denied" else f"对方没有授权（{error[:60]}）"
            raise LoginFailed(f"{server.title}：{reason}", pending.return_to)
        if server.auth != "oauth" or not code or len(code) > MAX_PARAM or pending.expires_at <= datetime.now(UTC):
            raise LoginFailed(f"{server.title}：这次授权已经过期或服务改了设置，请重新连接", pending.return_to)
        try:
            verifier = self._box.open(pending.verifier_ciphertext, context=_context(workspace_id, pending.state_hash))
            auth_server = auth_server_of(server)
            self._protocol.check_iss(auth_server, iss)
            tokens = await self._protocol.exchange(auth_server, self._oauth_client(server), code=code,
                                                   verifier=verifier, redirect_uri=self.redirect_uri)
        except (OAuthFailure, SecretError) as failure:
            raise LoginFailed(f"{server.title}：{failure}", pending.return_to) from failure
        await self._connections.save_login(workspace_id, server.id, user_id, tokens)
        if server.synced_at is None:
            await self._first_sync(server, tokens)
        return LoginDone(server_id=server.id, title=server.title, return_to=pending.return_to)

    async def _consume(self, workspace_id: str, state: str) -> Pending:
        """找到并删掉那条待完成记录：不管后面成不成功，同一个 state 只能用一次。"""
        async with self._session(workspace_id) as session:
            row = await session.get(McpOAuthPendingRow, digest(state))
            if row is None:
                raise LoginFailed("这次授权已经用过或过期了，请回到「设置 → 连接」重新连接")
            pending = Pending(state_hash=row.state_hash, server_id=row.server_id, user_id=row.user_id,
                              verifier_ciphertext=row.verifier_ciphertext, return_to=row.return_to,
                              expires_at=row.expires_at)
            await session.delete(row)
        return pending

    async def _first_sync(self, server: McpServerRecord, tokens: Tokens) -> None:
        """管理员登记完第一次登录：用他的令牌拉工具清单（照对方注解预填三档；收为企业 MCP 时先放了来源的工具，
        按原名保留它们的设置）。拉不到只记原因，连接照样算成功。"""
        endpoint = Endpoint(url=server.url, headers={"Authorization": f"Bearer {tokens.access_token}"},
                            timeout_seconds=server.timeout_seconds)
        try:
            remote = await self._client.list_tools(endpoint)
        except McpUnavailable as error:
            await self._store.update(server.workspace_id, server.id, McpChanges(sync_error=str(error)))
            return
        await self._store.update(server.workspace_id, server.id, McpChanges(
            tools=catalog.build(server.name, remote, list(server.tools), first=True), synced_at=datetime.now(UTC),
            clear_sync_error=True))

    # ---- 调用 ----

    async def bearer(self, workspace_id: str, server: McpServerRecord, user_id: str, *,
                     stale: str | None = None) -> str | None:
        """这个人此刻能用的访问令牌（快过期、或 stale 刚被拒绝就先刷新）；没连接或已失效返回 None；
        连不上对方令牌地址抛 OAuthFailure。"""
        auth_server, client = auth_server_of(server), self._oauth_client(server)
        return await self._connections.access_token(
            workspace_id, server.id, user_id,
            lambda refresh_token: self._protocol.refresh(auth_server, client, refresh_token), stale=stale)

    def _oauth_client(self, server: McpServerRecord) -> OAuthClient:
        plain = self._store.oauth_client(server)
        if plain is None:
            raise OAuthFailure("这个服务的 OAuth 应用还没配置好，请联系管理员")
        return OAuthClient.load(plain)
