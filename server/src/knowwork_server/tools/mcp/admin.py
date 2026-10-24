"""MCP 服务的登记、修改、同步与工具设置。

- 全部操作要 workspace:edit（空间 owner、admin），与新建知识源同一口径；成员用不用得上某个工具由 tool 授权决定；
- 新建、改地址或请求头时先连一次、拉工具清单，连不上就什么都不改（Unreachable），不存半成品；
- 手动刷新连不上时保留原来的工具清单，只记下失败原因，正在用的会话不受影响；
- 停用服务或工具立即生效：ToolService 执行前会按注册表此刻的判定重查。
"""

import logging
from dataclasses import replace
from datetime import UTC, datetime

from . import catalog
from .catalog import CatalogTool
from .client import Endpoint, McpClient, McpUnavailable
from .errors import Forbidden, NotFound, Unreachable
from .naming import PREFIX
from .schemas import McpServerCreate, McpServerPatch, McpToolPatch
from .store import McpChanges, McpServerRecord, McpServerStore

log = logging.getLogger(__name__)

WORKSPACE = "workspace"


class McpAdmin:
    def __init__(self, store: McpServerStore, client: McpClient, access) -> None:
        self._store = store
        self._client = client
        self._access = access

    async def list_servers(self, actor: str, workspace_id: str) -> list[McpServerRecord]:
        self._require(actor, workspace_id)
        return await self._store.list_servers(workspace_id)

    async def get(self, actor: str, workspace_id: str, server_id: str) -> McpServerRecord:
        self._require(actor, workspace_id)
        return await self._store.get(workspace_id, server_id)

    async def create(self, actor: str, workspace_id: str, body: McpServerCreate) -> McpServerRecord:
        self._require(actor, workspace_id)
        endpoint = Endpoint(url=body.url, headers=body.headers, timeout_seconds=body.timeout_seconds)
        tools = await self._fetch(body.name, endpoint, [])
        return await self._store.create(workspace_id, name=body.name, title=body.title, url=body.url,
                                        headers=body.headers, timeout_seconds=body.timeout_seconds, tools=tools,
                                        synced_at=_now(), created_by=actor)

    async def update(self, actor: str, workspace_id: str, server_id: str, body: McpServerPatch) -> McpServerRecord:
        self._require(actor, workspace_id)
        current = await self._store.get(workspace_id, server_id)
        changes = McpChanges(title=body.title, url=body.url, headers=body.headers,
                             timeout_seconds=body.timeout_seconds, enabled=body.enabled)
        if body.url is not None or body.headers is not None:
            endpoint = Endpoint(url=body.url or current.url,
                                headers=self._store.headers(current) if body.headers is None else body.headers,
                                timeout_seconds=body.timeout_seconds or current.timeout_seconds)
            tools = await self._fetch(current.name, endpoint, list(current.tools))
            changes = replace(changes, tools=tools, synced_at=_now(), clear_sync_error=True)
        return await self._store.update(workspace_id, server_id, changes)

    async def sync(self, actor: str, workspace_id: str, server_id: str) -> McpServerRecord:
        self._require(actor, workspace_id)
        current = await self._store.get(workspace_id, server_id)
        endpoint = Endpoint(url=current.url, headers=self._store.headers(current),
                            timeout_seconds=current.timeout_seconds)
        try:
            tools = await self._fetch(current.name, endpoint, list(current.tools))
        except Unreachable as error:
            await self._store.update(workspace_id, server_id, McpChanges(sync_error=str(error)))
            raise
        return await self._store.update(workspace_id, server_id,
                                        McpChanges(tools=tools, synced_at=_now(), clear_sync_error=True))

    async def configure_tool(self, actor: str, workspace_id: str, server_id: str, tool_name: str,
                             body: McpToolPatch) -> McpServerRecord:
        self._require(actor, workspace_id)
        current = await self._store.get(workspace_id, server_id)
        target = next((tool for tool in current.tools if tool.name == tool_name), None)
        if target is None:
            raise NotFound("这个 MCP 服务没有该工具")
        tools = catalog.configure(list(current.tools), target.raw_name, risk=body.risk, approval=body.approval,
                                  enabled=body.enabled)
        return await self._store.update(workspace_id, server_id, McpChanges(tools=tools))

    async def delete(self, actor: str, workspace_id: str, server_id: str) -> None:
        """连同它的工具授权一起删（同一事务）：单个工具的 tool:mcp__<服务名>__<工具> 与整个服务的 …__*。"""
        self._require(actor, workspace_id)
        change = await self._store.delete(workspace_id, server_id, also=lambda session, name: self._access.purge_grants(
            session, workspace_id, prefixes=(f"tool:{PREFIX}{name}__",)))
        await self._access.settle(change)

    async def _fetch(self, server_name: str, endpoint: Endpoint, previous: list[CatalogTool]) -> list[CatalogTool]:
        try:
            remote = await self._client.list_tools(endpoint)
        except McpUnavailable as error:
            raise Unreachable(str(error)) from error
        return catalog.build(server_name, remote, previous)

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

