"""插件：管理员给空间定每个预置插件的档，成员在「可选」的里面自己开关；每次编运行配置时算出这个人生效的插件。

一个人实际生效的预置插件 = 管理员定为全员启用的 + 管理员定为可选、且他自己开着的。
没有任何空间设过档时，一条库都不查（清单为空同理）：可选功能没用上时热路径不变。
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..db.base import in_workspace
from .catalog import CATALOG, CatalogCommand, CatalogComponent, CatalogPlugin
from .models import MODE_OFF, MODE_ON, MODE_OPTIONAL, MODES, SOURCE_BUILTIN, PluginInstallation, PluginSwitch

WORKSPACE = "workspace"


class AccessChecker(Protocol):
    def check(self, user_id: str, workspace_id: str, resource: str, action: str) -> bool: ...


class Forbidden(Exception):
    pass


class Invalid(ValueError):
    """请求不合规则；message 直接给人看。"""


@dataclass(frozen=True)
class WorkspacePlugin:
    """管理员看到的一行。"""

    key: str
    name: str
    description: str
    source: str
    mode: str
    detail: str = ""  # 插件详情里的说明文档
    components: tuple[CatalogComponent, ...] = ()


@dataclass(frozen=True)
class MyPlugin:
    """成员看到的一行：locked 是管理员定了全员启用、自己不能关。"""

    key: str
    name: str
    description: str
    source: str
    enabled: bool
    locked: bool
    commands: tuple[CatalogCommand, ...] = ()  # 这个插件带的 / 命令（新对话页列命令用）
    detail: str = ""
    components: tuple[CatalogComponent, ...] = ()


class PluginService:
    def __init__(self, sessionmaker: async_sessionmaker, access: AccessChecker, *,
                 catalog: Sequence[CatalogPlugin] = CATALOG) -> None:
        self._sessions = sessionmaker
        self._access = access
        self._catalog = tuple(catalog)
        self._by_key = {plugin.key: plugin for plugin in self._catalog}

    # ---- 读库 ----

    async def _modes(self, workspace_id: str) -> dict[str, str]:
        """这个空间给各预置插件定的档；没设过的不在里面（就是关闭）。清单以外的旧键不算。"""
        with in_workspace(workspace_id):
            async with self._sessions() as session:
                rows = await session.execute(
                    select(PluginInstallation.plugin, PluginInstallation.mode).where(
                        PluginInstallation.workspace_id == workspace_id, PluginInstallation.owner_user_id == "",
                        PluginInstallation.source == SOURCE_BUILTIN))
        return {plugin: mode for plugin, mode in rows if plugin in self._by_key}

    async def _switched_on(self, workspace_id: str, user_id: str) -> frozenset[str]:
        with in_workspace(workspace_id):
            async with self._sessions() as session:
                rows = await session.scalars(select(PluginSwitch.plugin).where(
                    PluginSwitch.workspace_id == workspace_id, PluginSwitch.user_id == user_id,
                    PluginSwitch.enabled.is_(True)))
        return frozenset(rows)

    # ---- 管理员 ----

    def _require_admin(self, actor: str, workspace_id: str) -> None:
        if not self._access.check(actor, workspace_id, WORKSPACE, "edit"):
            raise Forbidden()

    async def workspace_plugins(self, actor: str, workspace_id: str) -> tuple[WorkspacePlugin, ...]:
        self._require_admin(actor, workspace_id)
        modes = await self._modes(workspace_id) if self._catalog else {}
        return tuple(WorkspacePlugin(key=p.key, name=p.name, description=p.description, source=SOURCE_BUILTIN,
                                     mode=modes.get(p.key, MODE_OFF), detail=p.detail, components=p.components)
                     for p in self._catalog)

    async def set_mode(self, actor: str, workspace_id: str, key: str, mode: str) -> WorkspacePlugin:
        self._require_admin(actor, workspace_id)
        plugin = self._by_key.get(key)
        if plugin is None:
            raise Invalid("没有这个插件")
        if mode not in MODES:
            raise Invalid("档位只能是关闭、全员启用或可选")
        values = {"mode": mode, "updated_by": actor, "updated_at": func.now()}
        with in_workspace(workspace_id):
            async with self._sessions.begin() as session:
                await session.execute(
                    insert(PluginInstallation)
                    .values(id=f"pi_{uuid.uuid4().hex}", workspace_id=workspace_id, owner_user_id="", plugin=key,
                            source=SOURCE_BUILTIN, **values)
                    .on_conflict_do_update(constraint="uq_plugin_installation", set_=values))
        return WorkspacePlugin(key=key, name=plugin.name, description=plugin.description, source=SOURCE_BUILTIN,
                               mode=mode, detail=plugin.detail, components=plugin.components)

    # ---- 成员 ----

    def _require_member(self, actor: str, workspace_id: str) -> None:
        if not self._access.check(actor, workspace_id, WORKSPACE, "use"):
            raise Forbidden()

    async def my_plugins(self, actor: str, workspace_id: str) -> tuple[MyPlugin, ...]:
        """管理员给了这个人的插件：全员启用的（锁着）和可选的（带他自己的开关）。关闭的不列。"""
        self._require_member(actor, workspace_id)
        modes = await self._modes(workspace_id) if self._catalog else {}
        if not modes:
            return ()
        on = await self._switched_on(workspace_id, actor) if MODE_OPTIONAL in modes.values() else frozenset()
        return tuple(MyPlugin(key=p.key, name=p.name, description=p.description, source=SOURCE_BUILTIN,
                              enabled=modes[p.key] == MODE_ON or p.key in on, locked=modes[p.key] == MODE_ON,
                              commands=p.commands, detail=p.detail, components=p.components)
                     for p in self._catalog if modes.get(p.key, MODE_OFF) != MODE_OFF)

    async def switch(self, actor: str, workspace_id: str, key: str, enabled: bool) -> MyPlugin:
        self._require_member(actor, workspace_id)
        plugin = self._by_key.get(key)
        mode = (await self._modes(workspace_id)).get(key, MODE_OFF) if plugin is not None else MODE_OFF
        if plugin is None or mode == MODE_OFF:
            raise Invalid("没有这个插件，或管理员没有启用它")
        if mode == MODE_ON:
            raise Invalid("这个插件由管理员为全员启用，不能自己关")
        with in_workspace(workspace_id):
            async with self._sessions.begin() as session:
                await session.execute(
                    insert(PluginSwitch).values(workspace_id=workspace_id, user_id=actor, plugin=key, enabled=enabled)
                    .on_conflict_do_update(index_elements=["workspace_id", "user_id", "plugin"],
                                           set_={"enabled": enabled, "updated_at": func.now()}))
        return MyPlugin(key=key, name=plugin.name, description=plugin.description, source=SOURCE_BUILTIN,
                        enabled=enabled, locked=False, commands=plugin.commands, detail=plugin.detail,
                        components=plugin.components)

    # ---- 编运行配置时 ----

    async def effective(self, workspace_id: str, user_id: str) -> tuple[str, ...]:
        """这个人此刻生效的预置插件键（排好序）。每一轮编运行配置都会调：清单为空不查库；空间没设过档只查一次。"""
        if not self._catalog:
            return ()
        modes = await self._modes(workspace_id)
        if not modes:
            return ()
        optional = {key for key, mode in modes.items() if mode == MODE_OPTIONAL}
        on = await self._switched_on(workspace_id, user_id) if optional else frozenset()
        return tuple(sorted(key for key, mode in modes.items() if mode == MODE_ON or (key in optional and key in on)))
