"""mcp_server 的读写。每个事务先设置行级安全变量，只看得到本空间的行。

企业服务（scope = enterprise）和默认空间自己的服务存在同一个空间名下：列表按范围分开（list_servers 只给空间服务），
免得默认空间的管理员看到、改到企业服务；按 id 取（get）不分范围，由调用方核对范围。

请求头与 OAuth 授权的 client 在这里加解密：入库前整体 seal，只有 headers()、oauth_client() 返回明文（连接时用）。
"""

import json
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, fields
from datetime import datetime
from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...access.rls import set_workspace
from ...knowledge.crypto import SecretBox, mask
from .catalog import CatalogTool, dump, load
from .errors import Conflict, NotFound
from .models import McpServerRow

SECRET_PURPOSE = "mcp-secrets"
T = TypeVar("T")


@dataclass(frozen=True)
class McpServerRecord:
    """一行的不可变快照；只带密文，不带请求头明文。"""

    id: str
    workspace_id: str
    name: str
    title: str
    url: str
    headers_ciphertext: str | None
    header_names: tuple[dict[str, str], ...]
    timeout_seconds: int
    enabled: bool
    tools: tuple[CatalogTool, ...]
    synced_at: datetime | None
    sync_error: str | None
    created_by: str
    updated_at: datetime | None
    auth: str = "shared"  # shared | user_token | oauth
    auth_config: dict | None = None
    oauth_client_ciphertext: str | None = None
    scope: str = "workspace"  # workspace | enterprise
    audience: tuple[str, ...] = ()  # 企业服务发给哪些空间
    origin_server_id: str | None = None  # 企业服务从哪个空间服务收上来的

    @property
    def per_user(self) -> bool:
        """每人用自己的凭据（个人访问令牌、OAuth 授权）。"""
        return self.auth != "shared"


@dataclass(frozen=True)
class McpChanges:
    """要改的字段；None 表示不改。headers 给出时整体替换（空字典表示清空）。"""

    title: str | None = None
    url: str | None = None
    headers: dict[str, str] | None = None
    timeout_seconds: int | None = None
    enabled: bool | None = None
    tools: list[CatalogTool] | None = None
    synced_at: datetime | None = None
    sync_error: str | None = None
    clear_sync_error: bool = False
    auth: str | None = None
    auth_config: dict | None = None
    oauth_client: str | None = None  # OAuth 授权的 client（明文 JSON，入库前加密）；给出时替换
    clear_oauth_client: bool = False
    audience: list[str] | None = None  # 企业服务发给哪些空间


def new_server_id() -> str:
    return "mcp_" + secrets.token_hex(8)


def _record(row: McpServerRow) -> McpServerRecord:
    return McpServerRecord(id=row.id, workspace_id=row.workspace_id, name=row.name, title=row.title, url=row.url,
                           headers_ciphertext=row.headers_ciphertext, header_names=tuple(row.header_names or ()),
                           timeout_seconds=row.timeout_seconds, enabled=row.enabled, tools=tuple(load(row.tools)),
                           synced_at=row.synced_at, sync_error=row.sync_error, created_by=row.created_by,
                           updated_at=row.updated_at, auth=row.auth or "shared",
                           auth_config=dict(row.auth_config) if row.auth_config else None,
                           oauth_client_ciphertext=row.oauth_client_ciphertext, scope=row.scope or "workspace",
                           audience=tuple(row.audience or ()), origin_server_id=row.origin_server_id)


def headers_context(workspace_id: str, server_id: str) -> str:
    return f"{workspace_id}/mcp/{server_id}/headers"


def oauth_client_context(workspace_id: str, server_id: str) -> str:
    return f"{workspace_id}/mcp/{server_id}/oauth-client"


class McpServerStore:
    def __init__(self, sessionmaker: async_sessionmaker, box: SecretBox) -> None:
        self._sessionmaker = sessionmaker
        self._box = box

    @asynccontextmanager
    async def _session(self, workspace_id: str) -> AsyncIterator[AsyncSession]:
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            yield session

    async def create(self, workspace_id: str, *, name: str, title: str, url: str, headers: dict[str, str],
                     timeout_seconds: int, tools: list[CatalogTool], synced_at: datetime | None, created_by: str,
                     also: Callable[[AsyncSession, str], Awaitable[T]], auth: str = "shared",
                     auth_config: dict | None = None, oauth_client: str | None = None,
                     server_id: str | None = None, scope: str = "workspace",
                     origin_server_id: str | None = None) -> tuple[McpServerRecord, T]:
        """新建服务；also(session, 服务名) 在同一事务里执行（默认给全体成员用），返回它的结果。"""
        server_id = server_id or new_server_id()
        row = McpServerRow(id=server_id, workspace_id=workspace_id, name=name, title=title, url=url,
                           timeout_seconds=timeout_seconds, enabled=True, tools=dump(tools), synced_at=synced_at,
                           created_by=created_by, auth=auth, auth_config=auth_config, scope=scope,
                           origin_server_id=origin_server_id)
        self._seal(row, headers)
        if oauth_client is not None:
            row.oauth_client_ciphertext = self._box.seal(oauth_client,
                                                         context=oauth_client_context(workspace_id, server_id))
        try:
            async with self._session(workspace_id) as session:
                session.add(row)
                await session.flush()
                extra = await also(session, name)
        except IntegrityError as error:
            raise Conflict(f"已经有一个叫 {name} 的 MCP 服务") from error
        return await self.get(workspace_id, server_id), extra

    async def update(self, workspace_id: str, server_id: str, changes: McpChanges) -> McpServerRecord:
        async with self._session(workspace_id) as session:
            row = await self._row(session, server_id)
            for item in fields(McpChanges):
                value = getattr(changes, item.name)
                if value is None or item.name in ("headers", "tools", "clear_sync_error", "oauth_client",
                                                  "clear_oauth_client"):
                    continue
                setattr(row, item.name, value)
            if changes.headers is not None:
                self._seal(row, changes.headers)
            if changes.tools is not None:
                row.tools = dump(changes.tools)
            if changes.clear_sync_error:
                row.sync_error = None
            if changes.clear_oauth_client:
                row.oauth_client_ciphertext = None
            if changes.oauth_client is not None:
                row.oauth_client_ciphertext = self._box.seal(changes.oauth_client,
                                                             context=oauth_client_context(workspace_id, server_id))
        return await self.get(workspace_id, server_id)

    async def delete(self, workspace_id: str, server_id: str,
                     also: Callable[[AsyncSession, str], Awaitable[T]]) -> T:
        """删除服务；also(session, 服务名) 在同一事务里执行（删掉它的工具授权），返回它的结果。"""
        async with self._session(workspace_id) as session:
            row = await self._row(session, server_id)
            await session.delete(row)
            return await also(session, row.name)

    async def get(self, workspace_id: str, server_id: str) -> McpServerRecord:
        async with self._session(workspace_id) as session:
            return _record(await self._row(session, server_id))

    async def list_servers(self, workspace_id: str, scope: str = "workspace") -> list[McpServerRecord]:
        async with self._session(workspace_id) as session:
            rows = (await session.execute(select(McpServerRow).where(McpServerRow.scope == scope)
                                          .order_by(McpServerRow.created_at))).scalars()
            return [_record(row) for row in rows]

    def headers(self, record: McpServerRecord) -> dict[str, str]:
        if not record.headers_ciphertext:
            return {}
        plain = self._box.open(record.headers_ciphertext, context=headers_context(record.workspace_id, record.id))
        return dict(json.loads(plain))

    def oauth_client(self, record: McpServerRecord) -> str | None:
        if not record.oauth_client_ciphertext:
            return None
        return self._box.open(record.oauth_client_ciphertext,
                              context=oauth_client_context(record.workspace_id, record.id))

    def _seal(self, row: McpServerRow, headers: dict[str, str]) -> None:
        row.header_names = [{"name": key, "last4": mask(value)} for key, value in headers.items()]
        row.headers_ciphertext = (self._box.seal(json.dumps(headers), context=headers_context(row.workspace_id, row.id))
                                  if headers else None)

    @staticmethod
    async def _row(session: AsyncSession, server_id: str) -> McpServerRow:
        row = await session.get(McpServerRow, server_id)
        if row is None:
            raise NotFound()
        return row

