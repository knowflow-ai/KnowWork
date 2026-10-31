"""每人的 MCP 连接：认证方式是个人访问令牌或 OAuth 授权时，每个成员用自己的凭据调用这个服务。

- 凭据加密存（AES-256-GCM，附加数据绑定空间、服务与用户，挪到别人名下解不开），只在调用的那一刻解开；
  个人访问令牌存 {"token"}，OAuth 授权存 {"access_token", "refresh_token"}，过期时间在 expires_at；
  display 只给人看（令牌后四位），任何接口都不返回明文；
- 登录令牌快过期时在行锁里刷新（renew）：同一个人同时发起的几次调用只刷新一次，轮换式的刷新令牌不会被用两次；
- 对方拒绝过这份凭据（401、403）就标成 invalid：对话里不再给这个服务的工具，成员在「设置 → 连接」里重新连接；
- 成员被移出空间时删掉他在这个空间的服务上的连接（forget_member，在移出的同一个事务里）；服务删了随外键一起删；
- 企业服务的连接存在默认空间名下（服务在哪个空间，连接就在哪个空间），一个人一份，换空间不用重连。
"""

import json
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...access.rls import set_workspace
from ...knowledge.crypto import SecretBox, SecretError, mask
from .models import McpConnectionRow, McpServerRow
from .oauth import OAuthFailure, Tokens

RENEW_MARGIN = timedelta(seconds=60)  # 离过期不到这么久就先刷新，免得调用到一半过期


@dataclass(frozen=True)
class Connection:
    server_id: str
    user_id: str
    kind: str  # token | oauth
    display: str
    status: str  # ok | invalid
    connected_at: datetime | None

    @property
    def usable(self) -> bool:
        return self.status == "ok"


def _context(workspace_id: str, server_id: str, user_id: str) -> str:
    return f"{workspace_id}/mcp/{server_id}/connection/{user_id}"


class McpConnections:
    def __init__(self, sessionmaker: async_sessionmaker, box: SecretBox) -> None:
        self._sessionmaker = sessionmaker
        self._box = box

    @asynccontextmanager
    async def _session(self, workspace_id: str) -> AsyncIterator[AsyncSession]:
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            yield session

    def _seal(self, workspace_id: str, server_id: str, user_id: str, secret: dict[str, str | None]) -> str:
        return self._box.seal(json.dumps(secret), context=_context(workspace_id, server_id, user_id))

    def _open(self, row: McpConnectionRow) -> dict | None:
        try:
            return dict(json.loads(self._box.open(row.secret_ciphertext,
                                                  context=_context(row.workspace_id, row.server_id, row.user_id))))
        except (SecretError, ValueError, TypeError):
            return None

    async def save(self, workspace_id: str, server_id: str, user_id: str, token: str, *,
                   session: AsyncSession | None = None) -> None:
        """个人访问令牌：连接或重新连接（覆盖原来的）。session 给出时在调用方的事务里写（登记服务时存管理员自己的连接）。"""
        await self._upsert(workspace_id, server_id, user_id, kind="token", secret={"token": token},
                           display=mask(token), expires_at=None, session=session)

    async def save_login(self, workspace_id: str, server_id: str, user_id: str, tokens: Tokens) -> None:
        """OAuth 授权：回调换到令牌后存（覆盖原来的）。"""
        await self._upsert(workspace_id, server_id, user_id, kind="oauth",
                           secret={"access_token": tokens.access_token, "refresh_token": tokens.refresh_token},
                           display="", expires_at=tokens.expires_at, session=None)

    async def _upsert(self, workspace_id: str, server_id: str, user_id: str, *, kind: str, secret: dict,
                      display: str, expires_at: datetime | None, session: AsyncSession | None) -> None:
        values = {"server_id": server_id, "user_id": user_id, "workspace_id": workspace_id, "kind": kind,
                  "secret_ciphertext": self._seal(workspace_id, server_id, user_id, secret),
                  "display": display, "status": "ok", "expires_at": expires_at}
        statement = insert(McpConnectionRow).values(**values).on_conflict_do_update(
            index_elements=["server_id", "user_id"],
            set_={**{key: values[key] for key in ("kind", "secret_ciphertext", "display", "status", "expires_at")},
                  "connected_at": func.now(), "updated_at": func.now()})
        if session is not None:
            await session.execute(statement)
            return
        async with self._session(workspace_id) as own:
            await own.execute(statement)

    async def token(self, workspace_id: str, server_id: str, user_id: str) -> str | None:
        """个人访问令牌：这个人能用的令牌；没连接、已失效、不是令牌连接或解不开都是 None。"""
        async with self._session(workspace_id) as session:
            row = await session.get(McpConnectionRow, (server_id, user_id))
        secret = self._open(row) if row is not None and row.status == "ok" and row.kind == "token" else None
        return str(secret["token"]) if secret and secret.get("token") else None

    async def access_token(self, workspace_id: str, server_id: str, user_id: str,
                           refresh: Callable[[str], Awaitable[Tokens]], *, stale: str | None = None) -> str | None:
        """OAuth 授权：这个人能用的访问令牌，快过期时先用刷新令牌换新的（refresh 由调用方给，去对方令牌地址）。
        stale：刚被对方拒绝的访问令牌（对方没给过期时间、或提前作废了它），还是它就换一张新的。
        对方拒绝了刷新令牌就把连接标成失效、返回 None；连不上对方时抛 OAuthFailure，连接不动。"""
        async with self._session(workspace_id) as session:
            row = await session.get(McpConnectionRow, (server_id, user_id))
            secret = self._usable_login(row)
            if secret is None:
                return None
            if not _due(row.expires_at, secret, stale):
                return str(secret["access_token"])
        return await self._renew(workspace_id, server_id, user_id, refresh, stale)

    async def _renew(self, workspace_id: str, server_id: str, user_id: str,
                     refresh: Callable[[str], Awaitable[Tokens]], stale: str | None) -> str | None:
        async with self._session(workspace_id) as session:
            row = await session.get(McpConnectionRow, (server_id, user_id), with_for_update=True)
            secret = self._usable_login(row)
            if secret is None:
                return None
            if not _due(row.expires_at, secret, stale):
                return str(secret["access_token"])  # 等锁的时候别人已经刷新过了
            try:
                tokens = await refresh(str(secret["refresh_token"]))
            except OAuthFailure as error:
                if not error.rejected:
                    raise
                row.status = "invalid"
                return None
            row.secret_ciphertext = self._seal(workspace_id, server_id, user_id,
                                               {"access_token": tokens.access_token,
                                                "refresh_token": tokens.refresh_token})
            row.expires_at = tokens.expires_at
            return tokens.access_token

    def _usable_login(self, row: McpConnectionRow | None) -> dict | None:
        if row is None or row.status != "ok" or row.kind != "oauth":
            return None
        secret = self._open(row)
        return secret if secret and secret.get("access_token") else None

    async def for_user(self, workspace_id: str, user_id: str) -> dict[str, Connection]:
        async with self._session(workspace_id) as session:
            rows = (await session.scalars(select(McpConnectionRow).where(
                McpConnectionRow.workspace_id == workspace_id, McpConnectionRow.user_id == user_id))).all()
        return {row.server_id: Connection(server_id=row.server_id, user_id=row.user_id, kind=row.kind,
                                          display=row.display, status=row.status, connected_at=row.connected_at)
                for row in rows}

    async def for_servers(self, servers: Iterable[Any], user_id: str) -> dict[str, Connection]:
        """这个人在这些服务上的连接（服务 id → 连接）。连接按服务所在的空间存（企业服务在默认空间名下），
        所以按各服务的空间分别读；不要凭据的服务不读。"""
        wanted = {server.id: server.workspace_id for server in servers if server.per_user}
        found: dict[str, Connection] = {}
        for home in sorted(set(wanted.values())):
            found.update({server_id: connection for server_id, connection in (await self.for_user(home, user_id)).items()
                          if wanted.get(server_id) == home})
        return found

    async def mark_invalid(self, workspace_id: str, server_id: str, user_id: str) -> None:
        async with self._session(workspace_id) as session:
            await session.execute(update(McpConnectionRow).where(
                McpConnectionRow.server_id == server_id, McpConnectionRow.user_id == user_id).values(status="invalid"))

    async def delete(self, workspace_id: str, server_id: str, user_id: str) -> bool:
        async with self._session(workspace_id) as session:
            result = await session.execute(delete(McpConnectionRow).where(
                McpConnectionRow.server_id == server_id, McpConnectionRow.user_id == user_id))
        return bool(result.rowcount)

    async def forget_server(self, workspace_id: str, server_id: str) -> None:
        """服务的认证方式或 OAuth 应用变了：原来的连接都作废，成员要按新的方式重新连接。"""
        async with self._session(workspace_id) as session:
            await session.execute(delete(McpConnectionRow).where(McpConnectionRow.server_id == server_id))

    @staticmethod
    async def forget_member(session: AsyncSession, workspace_id: str, user_id: str) -> None:
        """成员被移出空间（AccessService.on_member_removed，在移出的事务里）：删掉他在这个空间的服务上的连接。
        企业服务的连接也存在默认空间名下，但它是跟人走的（他可能还在别的空间用），移出默认空间时不删。"""
        own = select(McpServerRow.id).where(McpServerRow.scope == "workspace")
        await session.execute(delete(McpConnectionRow).where(McpConnectionRow.workspace_id == workspace_id,
                                                             McpConnectionRow.user_id == user_id,
                                                             McpConnectionRow.server_id.in_(own)))


def _expiring(expires_at: datetime | None) -> bool:
    return expires_at is not None and expires_at - datetime.now(UTC) <= RENEW_MARGIN


def _due(expires_at: datetime | None, secret: dict, stale: str | None) -> bool:
    """该刷新了：有刷新令牌，并且快过期了、或手上的正是刚被拒绝的那张。"""
    if not secret.get("refresh_token"):
        return False
    return _expiring(expires_at) or (stale is not None and secret.get("access_token") == stale)
