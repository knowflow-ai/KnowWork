"""企业 MCP：系统管理员登记一次，发给选定或全部空间（界面叫「企业」，代码里叫 enterprise）。

- 存法：mcp_server 里 scope = enterprise 的行，存在平台配置所在的默认空间名下，名字是 ent_<名字>（naming.ENTERPRISE_PREFIX）；
  audience 是发给哪些空间（"*" 是全部空间，以后新建的也有；刚建时为空，哪个空间都看不到）；
- 管理（EnterpriseMcpAdmin）沿用空间服务的那一套（admin.McpAdmin），只换范围、谁能管（系统管理员）、名字前缀与默认授权；
  接口与页面在扩展里（多空间才有意义），开源这边只给服务；
- 从空间 MCP 收为企业 MCP（promote）：照着那个空间服务新建一个企业服务，地址、认证方式等由页面预填、系统管理员确认，
  每个工具的三档与分组按原名带过来；凭据不带（共享凭据、OAuth 应用密钥由系统管理员重填，那是那个部门的账号），
  成员的连接也不带（企业服务的连接另存，每人重新连一次）；原来那份不动，由那个空间自己删；
- 到了空间（McpReach）：默认全体成员可用；空间管理员可以停用、收窄给谁用（EnterpriseMcpHere），设置记在
  mcp_workspace_setting，收窄时选中的对象存成本空间对 tool:mcp__ent_<名字>__* 的授权（管理员本来就能用全部）；
- 每人的连接按服务所在的空间（默认空间）存：一个人一份，换空间不用重连，被移出某个空间也不删（connections.forget_member）。
"""

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...access.errors import AccessError
from ...access.rls import set_workspace
from ...access.vocab import InvalidInput as AccessInvalid
from ...access.workspaces import PLATFORM_WORKSPACE_ID
from .admin import EVERYONE, McpAdmin
from .errors import Forbidden, InvalidInput, NotFound
from .models import McpWorkspaceSettingRow
from .naming import ENTERPRISE_PREFIX, server_tool_pattern
from .oauth_login import Probe
from .schemas import McpServerCreate, McpServerPatch, McpToolPatch
from .store import McpChanges, McpServerRecord, McpServerStore

SCOPE = "enterprise"
ALL_WORKSPACES = "*"
MAX_AUDIENCE = 500


def reaches(audience: Sequence[str], workspace_id: str) -> bool:
    return ALL_WORKSPACES in audience or workspace_id in audience


@dataclass(frozen=True)
class Setting:
    """空间管理员对一个企业服务的设置；没有记录就是默认（启用、全体成员）。"""

    off: bool = False
    narrowed: bool = False


DEFAULT = Setting()


def resource_of(server: McpServerRecord) -> str:
    """收窄时授权挂在哪：这个服务现在与以后的全部工具。"""
    return f"tool:{server_tool_pattern(server.name)}"


class _EnterpriseAdmin(McpAdmin):
    SCOPE = SCOPE

    def _require(self, actor: str, workspace_id: str) -> None:
        if workspace_id != PLATFORM_WORKSPACE_ID or not self._access.is_system_admin(actor):
            raise Forbidden("只有系统管理员可以管理企业 MCP")

    def _stored_name(self, name: str) -> str:
        return f"{ENTERPRISE_PREFIX}{name}"

    async def _seed(self, session, workspace_id: str, name: str):
        return None  # 到了空间默认全体成员可用，不靠授权


class EnterpriseMcpAdmin:
    """系统管理员管企业 MCP（给扩展的接口与页面用）。各方法照空间服务的同名方法，只是不用传空间。"""

    def __init__(self, store: McpServerStore, client, access, connections, login) -> None:
        self._inner = _EnterpriseAdmin(store, client, access, connections, login)
        self._store = store
        self._access = access

    @property
    def redirect_uri(self) -> str:
        return self._inner.redirect_uri

    async def probe(self, actor: str, url: str) -> Probe:
        return await self._inner.probe(actor, PLATFORM_WORKSPACE_ID, url)

    async def list_servers(self, actor: str) -> list[McpServerRecord]:
        return await self._inner.list_servers(actor, PLATFORM_WORKSPACE_ID)

    async def get(self, actor: str, server_id: str) -> McpServerRecord:
        return await self._inner.get(actor, PLATFORM_WORKSPACE_ID, server_id)

    async def create(self, actor: str, body: McpServerCreate) -> McpServerRecord:
        return await self._inner.create(actor, PLATFORM_WORKSPACE_ID, body)

    async def update(self, actor: str, server_id: str, body: McpServerPatch) -> McpServerRecord:
        return await self._inner.update(actor, PLATFORM_WORKSPACE_ID, server_id, body)

    async def sync(self, actor: str, server_id: str) -> McpServerRecord:
        return await self._inner.sync(actor, PLATFORM_WORKSPACE_ID, server_id)

    async def configure_tool(self, actor: str, server_id: str, tool_name: str, body: McpToolPatch) -> McpServerRecord:
        return await self._inner.configure_tool(actor, PLATFORM_WORKSPACE_ID, server_id, tool_name, body)

    async def delete(self, actor: str, server_id: str) -> None:
        """各空间的设置随外键一起删；各空间里收窄用的授权留着无害（服务名不会再指向它），不跨空间去清。"""
        await self._inner.delete(actor, PLATFORM_WORKSPACE_ID, server_id)

    async def promotable(self, actor: str) -> list[tuple[Any, McpServerRecord]]:
        """可以收为企业 MCP 的空间服务：全部空间里登记的（空间，服务），按空间排。"""
        if not self._access.is_system_admin(actor):
            raise Forbidden("只有系统管理员可以管理企业 MCP")
        found = []
        for workspace in await self._access.all_workspaces(actor):
            found.extend((workspace, server) for server in await self._store.list_servers(workspace.id))
        return found

    async def promote(self, actor: str, workspace_id: str, server_id: str, body: McpServerCreate) -> McpServerRecord:
        """把某个空间的 MCP 服务收为企业 MCP：照着它新建（工具三档跟着上来，凭据用 body 里系统管理员重填的），刚建时没发给任何空间。"""
        if not self._access.is_system_admin(actor):
            raise Forbidden("只有系统管理员可以管理企业 MCP")
        source = await self._store.get(workspace_id, server_id)
        if source.scope != "workspace":
            raise NotFound()
        return await self._inner.create(actor, PLATFORM_WORKSPACE_ID, body, template=source)

    async def set_audience(self, actor: str, server_id: str, workspaces: Sequence[str]) -> McpServerRecord:
        await self.get(actor, server_id)
        if ALL_WORKSPACES in workspaces:
            audience = [ALL_WORKSPACES]
        else:
            if len(workspaces) > MAX_AUDIENCE:
                raise InvalidInput(f"最多选 {MAX_AUDIENCE} 个空间")
            known = {summary.id for summary in await self._access.all_workspaces(actor)}
            unknown = [workspace for workspace in workspaces if workspace not in known]
            if unknown:
                raise InvalidInput(f"空间 {unknown[0]} 不存在")
            audience = sorted(set(workspaces))
        return await self._store.update(PLATFORM_WORKSPACE_ID, server_id, McpChanges(audience=audience))


class McpReach:
    """某个人在某个空间里看得到的 MCP 服务：本空间的，加上到达本空间、没停用的企业服务（收窄了的只留他能用的工具）。
    会话里的工具（registry）与「设置 → 连接」（me）都从这里取，判断只有一处。"""

    def __init__(self, sessionmaker: async_sessionmaker, store: McpServerStore, access) -> None:
        self._sessionmaker = sessionmaker
        self._store = store
        self._access = access

    async def servers(self, workspace_id: str, user_id: str) -> list[McpServerRecord]:
        own = await self._store.list_servers(workspace_id)
        settings = await self.settings(workspace_id)
        arrived = []
        for server in await self.arriving(workspace_id):
            setting = settings.get(server.id, DEFAULT)
            if setting.off:
                continue
            if setting.narrowed:  # 按工具判：授权页上按单个工具授的也算
                server = replace(server, tools=tuple(tool for tool in server.tools if self._access.check(
                    user_id, workspace_id, f"tool:{tool.name}", "use")))
            arrived.append(server)
        return [*own, *arrived]

    async def arriving(self, workspace_id: str) -> list[McpServerRecord]:
        """发到这个空间的企业服务（不管这个空间停没停用）。"""
        return [server for server in await self._store.list_servers(PLATFORM_WORKSPACE_ID, SCOPE)
                if reaches(server.audience, workspace_id)]

    async def enterprise(self, server_id: str) -> McpServerRecord | None:
        """按 id 找企业服务（不看发给了谁）；不是企业服务就是 None。"""
        return next((server for server in await self._store.list_servers(PLATFORM_WORKSPACE_ID, SCOPE)
                     if server.id == server_id), None)

    async def settings(self, workspace_id: str) -> dict[str, Setting]:
        async with self._session(workspace_id) as session:
            rows = (await session.scalars(select(McpWorkspaceSettingRow).where(
                McpWorkspaceSettingRow.workspace_id == workspace_id))).all()
        return {row.server_id: Setting(off=row.off, narrowed=row.narrowed) for row in rows}

    @asynccontextmanager
    async def _session(self, workspace_id: str) -> AsyncIterator[AsyncSession]:
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            yield session


@dataclass(frozen=True)
class HereView:
    """空间管理员「工具与 MCP → 企业」页签的一行。resource 是收窄时授权挂的资源。"""

    server_id: str
    title: str
    url: str
    auth: str
    tools: int  # 没禁用、能给模型的工具数
    off: bool
    narrowed: bool
    resource: str

    def to_json(self) -> dict[str, Any]:
        return {"serverId": self.server_id, "title": self.title, "url": self.url, "auth": self.auth,
                "tools": self.tools, "off": self.off, "narrowed": self.narrowed, "resource": self.resource}


class EnterpriseMcpHere:
    """空间管理员对到达本空间的企业服务：停用、收窄给谁用，不能改服务本身。"""

    def __init__(self, sessionmaker: async_sessionmaker, reach: McpReach, access) -> None:
        self._sessionmaker = sessionmaker
        self._reach = reach
        self._access = access

    def _require(self, actor: str, workspace_id: str) -> None:
        if not self._access.check(actor, workspace_id, "workspace", "edit"):
            raise Forbidden("只有空间管理员可以在本空间停用企业 MCP、改给谁用")

    async def list(self, actor: str, workspace_id: str) -> list[HereView]:
        self._require(actor, workspace_id)
        settings = await self._reach.settings(workspace_id)
        found = []
        for server in await self._reach.arriving(workspace_id):
            setting = settings.get(server.id, DEFAULT)
            found.append(HereView(server_id=server.id, title=server.title, url=server.url, auth=server.auth,
                                  tools=sum(1 for tool in server.tools if tool.usable), off=setting.off,
                                  narrowed=setting.narrowed, resource=resource_of(server)))
        return found

    async def set_off(self, actor: str, workspace_id: str, server_id: str, off: bool) -> None:
        self._require(actor, workspace_id)
        await self._arrived(workspace_id, server_id)
        await self._update(actor, workspace_id, server_id, off=off)

    async def set_users(self, actor: str, workspace_id: str, server_id: str, subjects: Sequence[str]) -> bool:
        """改给谁用：选了全体成员就是不收窄，否则收窄到选中的对象（一个都不选就只有管理员）。
        授权与标记分两步写，顺序保证中途失败也不会放大：收窄时先写授权再立标记，放开时先撤标记再清授权。返回是否收窄。"""
        self._require(actor, workspace_id)
        server = await self._arrived(workspace_id, server_id)
        narrowed = EVERYONE not in subjects
        if narrowed:
            await self._grant(actor, workspace_id, server, subjects)
            await self._update(actor, workspace_id, server_id, narrowed=True)
        else:
            await self._update(actor, workspace_id, server_id, narrowed=False)
            await self._grant(actor, workspace_id, server, ())
        return narrowed

    async def _arrived(self, workspace_id: str, server_id: str) -> McpServerRecord:
        server = next((s for s in await self._reach.arriving(workspace_id) if s.id == server_id), None)
        if server is None:
            raise NotFound("没有发到本空间的这个企业 MCP")
        return server

    async def _grant(self, actor: str, workspace_id: str, server: McpServerRecord, subjects: Sequence[str]) -> None:
        try:
            await self._access.set_users(actor, workspace_id, resource_of(server), subjects)
        except (AccessError, AccessInvalid) as error:  # 选的对象不存在、格式不对
            raise InvalidInput(str(error)) from error

    async def _update(self, actor: str, workspace_id: str, server_id: str, **changes: bool) -> None:
        """改一项设置；改完和默认一样就删掉那一行。"""
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            row = await session.get(McpWorkspaceSettingRow, (workspace_id, server_id), with_for_update=True)
            current = Setting(off=row.off, narrowed=row.narrowed) if row is not None else DEFAULT
            wanted = Setting(**{"off": current.off, "narrowed": current.narrowed, **changes})
            if wanted == DEFAULT:
                if row is not None:
                    await session.execute(delete(McpWorkspaceSettingRow).where(
                        McpWorkspaceSettingRow.workspace_id == workspace_id,
                        McpWorkspaceSettingRow.server_id == server_id))
            elif row is None:
                session.add(McpWorkspaceSettingRow(workspace_id=workspace_id, server_id=server_id, off=wanted.off,
                                                   narrowed=wanted.narrowed, updated_by=actor))
            else:
                row.off, row.narrowed, row.updated_by, row.updated_at = (wanted.off, wanted.narrowed, actor,
                                                                         func.now())
