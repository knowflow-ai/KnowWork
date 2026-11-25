"""上传的插件：空间插件（管理员上传、授权给全空间或一部分人）与个人插件（成员自己上传，只有本人用）。

一个人实际生效的上传插件 = 管理员定为默认启用且给了他的 + 管理员定为可选、给了他、他自己开着的
+ 他自己装并开着的（空间允许个人安装、包名不在禁用清单里）。同名时空间的优先；空间里已有同名插件时个人不能再装一份
（防止绕过授权）。「适用于」决定它装到哪：云端对话（这个人的专属宿主）、本机对话（他电脑上的桌面端）。

压缩包存对象存储、按内容摘要寻址；上传时只做静态检查（archive.py），不执行包里的代码。开了专属宿主时另在 worker 里
试装一遍（trial_job.py）：上传后先是「检查中」，通过了才可用。
"""

import asyncio
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from typing import Any, Protocol

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..db.base import in_workspace
from ..jobs import enqueue
from .archive import MAX_ARCHIVE_BYTES, ArchiveInvalid, Component, InspectedArchive, inspect_archive
from .catalog import CATALOG
from .models import (
    MODE_OFF,
    MODE_ON,
    MODE_OPTIONAL,
    MODES,
    SOURCE_UPLOAD,
    STATUS_CHECKING,
    STATUS_READY,
    TARGET_DEVICE,
    TARGETS,
    PluginInstallation,
    PluginSwitch,
    PluginWorkspaceSetting,
)
from .service import WORKSPACE, Forbidden, Invalid
from .trial_job import KIND as TRIAL_KIND

RESOURCE_TYPE = "plugin"
EVERYONE = "role:editor"  # 「全体成员」：同技能、MCP 的「给谁用」
SCOPE_WORKSPACE, SCOPE_PERSONAL = "workspace", "personal"
MAX_BLOCKED = 200
RESERVED_NAMES = frozenset(plugin.key for plugin in CATALOG)  # 预置插件的键，上传的插件不能用


class Access(Protocol):
    def check(self, user_id: str, workspace_id: str, resource: str, action: str) -> bool: ...

    async def set_users(self, actor_id: str, workspace_id: str, resource: str,
                        subjects: Sequence[str]) -> tuple[str, ...]: ...

    async def list_grants(self, actor_id: str, workspace_id: str, *, subject: str | None = None,
                          resource: str | None = None) -> tuple[Any, ...]: ...


class Blobs(Protocol):
    async def put(self, data: bytes) -> str: ...

    async def get(self, digest: str, *, max_bytes: int) -> bytes: ...


class NotFound(Exception):
    pass


@dataclass(frozen=True)
class PluginSettings:
    allow_personal: bool = True  # 允许成员安装个人插件
    allow_personal_device: bool = True  # 允许成员给本机对话装个人插件
    blocked: tuple[str, ...] = ()  # 禁用清单（包名）


@dataclass(frozen=True)
class Installed:
    """一个上传的插件。scope：workspace 空间插件、personal 个人插件。"""

    id: str
    name: str
    version: str
    description: str
    scope: str
    owner: str  # 个人插件的属主；空间插件为空
    mode: str  # 空间插件的档；个人插件恒为 optional（开关在本人手里）
    targets: tuple[str, ...]
    capabilities: tuple[str, ...]
    tools: tuple[str, ...]
    status: str
    reason: str
    size: int
    narrowed: bool = False
    subjects: tuple[str, ...] = ()  # 空间插件收窄后给了谁（只有管理员的列表里带）
    blocked: bool = False
    submitted: bool = False  # 个人插件：本人已经提交给管理员、等管理员转成空间插件
    updated_by: str | None = None
    title: str = ""  # 作者写的标题（插件页卡片上显示它，没有显示包名）
    components: tuple[dict, ...] = ()  # 组件清单（archive.Component）；说明文档太长，单独取（detail）


@dataclass(frozen=True)
class Mine:
    """「我的插件」里上传的一行。"""

    id: str
    name: str
    version: str
    description: str
    scope: str
    targets: tuple[str, ...]
    capabilities: tuple[str, ...]
    status: str
    reason: str
    enabled: bool
    locked: bool  # 管理员定了默认启用，自己不能关
    blocked: bool  # 包名在禁用清单里：开着也不生效
    submitted: bool = False  # 自己装的：已经提交给管理员
    title: str = ""
    components: tuple[dict, ...] = ()
    tools: tuple[str, ...] = ()  # 试装时看到它新增的工具


@dataclass(frozen=True)
class EffectiveUpload:
    """宿主（专属宿主或桌面端）要装的一个插件。"""

    id: str
    name: str
    version: str
    digest: str
    size: int


def _view(row: PluginInstallation, *, blocked: frozenset[str] = frozenset(),
          subjects: tuple[str, ...] = ()) -> Installed:
    return Installed(id=row.id, name=row.plugin, version=row.version, description=row.description,
                     scope=SCOPE_PERSONAL if row.owner_user_id else SCOPE_WORKSPACE, owner=row.owner_user_id,
                     mode=row.mode, targets=tuple(row.targets), capabilities=tuple(row.capabilities),
                     tools=tuple(row.tool_names), status=row.status, reason=row.status_reason,
                     size=row.archive_bytes, narrowed=row.narrowed, subjects=subjects,
                     blocked=bool(row.owner_user_id) and row.plugin in blocked,
                     submitted=row.submitted_at is not None, updated_by=row.updated_by, title=row.title,
                     components=tuple(row.components or ()))


def _targets(raw: Sequence[str] | None) -> list[str]:
    wanted = [target for target in TARGETS if target in (raw if raw is not None else TARGETS)]
    if not wanted or (raw is not None and any(item not in TARGETS for item in raw)):
        raise Invalid("「适用于」至少选一个：云端对话、本机对话")
    return wanted


class UploadService:
    def __init__(self, sessionmaker: async_sessionmaker, access: Access, blobs: Blobs, *,
                 cloud: bool = False) -> None:
        self._sessions = sessionmaker
        self._access = access
        self._blobs = blobs
        # 这个部署开没开个人专属宿主：没开时上传的插件在云端对话里没有地方跑，只在本机对话（桌面端）里生效；
        # 开了才试装（服务端有地方跑没审过的代码）
        self.cloud = cloud

    # ---- 权限 ----

    def _admin(self, user_id: str, workspace_id: str) -> bool:
        return self._access.check(user_id, workspace_id, WORKSPACE, "edit")

    def _require_admin(self, actor: str, workspace_id: str) -> None:
        if not self._admin(actor, workspace_id):
            raise Forbidden()

    def _require_member(self, actor: str, workspace_id: str) -> None:
        if not self._access.check(actor, workspace_id, WORKSPACE, "use"):
            raise Forbidden()

    def _granted(self, user_id: str, workspace_id: str, row: PluginInstallation) -> bool:
        """空间插件给没给这个人：没收窄是全空间；收窄后看资源授权，管理员一直有。"""
        return (not row.narrowed or self._admin(user_id, workspace_id)
                or self._access.check(user_id, workspace_id, f"{RESOURCE_TYPE}:{row.id}", "use"))

    # ---- 读库 ----

    async def _rows(self, workspace_id: str, *owners: str) -> list[PluginInstallation]:
        with in_workspace(workspace_id):
            async with self._sessions() as session:
                query = select(PluginInstallation).where(PluginInstallation.workspace_id == workspace_id,
                                                         PluginInstallation.source == SOURCE_UPLOAD)
                if owners:
                    query = query.where(PluginInstallation.owner_user_id.in_(owners))
                return list(await session.scalars(query.order_by(PluginInstallation.plugin)))

    async def _row(self, workspace_id: str, plugin_id: str) -> PluginInstallation:
        with in_workspace(workspace_id):
            async with self._sessions() as session:
                row = await session.scalar(select(PluginInstallation).where(
                    PluginInstallation.workspace_id == workspace_id, PluginInstallation.id == plugin_id,
                    PluginInstallation.source == SOURCE_UPLOAD))
        if row is None:
            raise NotFound()
        return row

    async def _switches(self, workspace_id: str, user_id: str) -> dict[str, bool]:
        with in_workspace(workspace_id):
            async with self._sessions() as session:
                rows = await session.execute(select(PluginSwitch.plugin, PluginSwitch.enabled).where(
                    PluginSwitch.workspace_id == workspace_id, PluginSwitch.user_id == user_id))
        return dict(rows.all())

    async def settings(self, workspace_id: str) -> PluginSettings:
        with in_workspace(workspace_id):
            async with self._sessions() as session:
                row = await session.get(PluginWorkspaceSetting, workspace_id)
        if row is None:
            return PluginSettings()
        return PluginSettings(allow_personal=row.allow_personal, allow_personal_device=row.allow_personal_device,
                              blocked=tuple(row.blocked))

    # ---- 管理员 ----

    async def set_settings(self, actor: str, workspace_id: str, settings: PluginSettings) -> PluginSettings:
        self._require_admin(actor, workspace_id)
        blocked = tuple(dict.fromkeys(name.strip() for name in settings.blocked if name.strip()))
        if len(blocked) > MAX_BLOCKED or any(len(name) > 128 for name in blocked):
            raise Invalid(f"禁用清单最多 {MAX_BLOCKED} 个包名，每个不超过 128 个字符")
        values = {"allow_personal": settings.allow_personal, "allow_personal_device": settings.allow_personal_device,
                  "blocked": list(blocked), "updated_by": actor, "updated_at": func.now()}
        with in_workspace(workspace_id):
            async with self._sessions.begin() as session:
                await session.execute(insert(PluginWorkspaceSetting).values(workspace_id=workspace_id, **values)
                                      .on_conflict_do_update(index_elements=["workspace_id"], set_=values))
        return replace(settings, blocked=blocked)

    async def workspace_list(self, actor: str, workspace_id: str) -> tuple[tuple[Installed, ...], tuple[Installed, ...]]:
        """（空间插件, 成员安装的个人插件）。空间插件带收窄后给了谁。"""
        self._require_admin(actor, workspace_id)
        rows = await self._rows(workspace_id)
        blocked = frozenset((await self.settings(workspace_id)).blocked)
        shared = []
        for row in (r for r in rows if not r.owner_user_id):
            subjects: tuple[str, ...] = ()
            if row.narrowed:
                grants = await self._access.list_grants(actor, workspace_id, resource=f"{RESOURCE_TYPE}:{row.id}")
                subjects = tuple(sorted(grant.subject for grant in grants if grant.action == "use"))
            shared.append(_view(row, subjects=subjects))
        return tuple(shared), tuple(_view(row, blocked=blocked) for row in rows if row.owner_user_id)

    async def update(self, actor: str, workspace_id: str, plugin_id: str, *, mode: str | None = None,
                     targets: Sequence[str] | None = None, subjects: Sequence[str] | None = None) -> Installed:
        """改空间插件的档、适用于、给谁用。给谁用：选了全体成员就是不收窄，否则收窄到选中的对象。
        授权与标记分两步写，顺序保证中途失败也不会放大：收窄时先写授权再立标记，放开时先撤标记再清授权。"""
        self._require_admin(actor, workspace_id)
        row = await self._row(workspace_id, plugin_id)
        if row.owner_user_id:
            raise Invalid("个人插件由本人管理；不想让它生效请把包名加进禁用清单")
        if mode is not None and mode not in MODES:
            raise Invalid("档位只能是关闭、默认启用或可选")
        changes: dict[str, Any] = {"updated_by": actor}
        if mode is not None:
            changes["mode"] = mode
        if targets is not None:
            changes["targets"] = _targets(targets)
        resource = f"{RESOURCE_TYPE}:{row.id}"
        if subjects is not None and EVERYONE not in subjects:
            await self._access.set_users(actor, workspace_id, resource, subjects)
            changes["narrowed"] = True
        elif subjects is not None:
            changes["narrowed"] = False
        await self._write(workspace_id, plugin_id, changes)
        if subjects is not None and EVERYONE in subjects:
            await self._access.set_users(actor, workspace_id, resource, ())
        fresh = await self._row(workspace_id, plugin_id)
        kept = tuple(sorted(subjects)) if subjects is not None and fresh.narrowed else ()
        return _view(fresh, subjects=kept)

    async def _write(self, workspace_id: str, plugin_id: str, changes: dict[str, Any]) -> None:
        with in_workspace(workspace_id):
            async with self._sessions.begin() as session:
                row = await session.get(PluginInstallation, plugin_id)
                if row is None or row.workspace_id != workspace_id:
                    raise NotFound()
                for key, value in changes.items():
                    setattr(row, key, value)

    # ---- 上传与删除（管理员传空间插件，成员传个人插件）----

    async def install(self, actor: str, workspace_id: str, scope: str, data: bytes,
                      targets: Sequence[str] | None = None) -> Installed:
        personal = scope == SCOPE_PERSONAL
        settings = await self.settings(workspace_id)
        if personal:
            self._require_member(actor, workspace_id)
            if not settings.allow_personal:
                raise Invalid("这个空间没有开放成员安装个人插件，请联系空间管理员")
        else:
            self._require_admin(actor, workspace_id)
        wanted = _targets(targets)
        if personal and not settings.allow_personal_device:
            wanted = [target for target in wanted if target != TARGET_DEVICE]
            if not wanted:
                raise Invalid("这个空间没有开放给本机对话安装个人插件")
        try:
            inspected = await asyncio.to_thread(inspect_archive, data)
        except ArchiveInvalid as error:
            raise Invalid(str(error)) from error
        if inspected.name in RESERVED_NAMES:
            # 预置插件的键和上传插件的包名在同一张登记表、同一份个人开关里：撞名会覆盖预置那一行、开关串在一起
            raise Invalid(f"插件包名 {inspected.name} 和预置插件重名，请在 package.json 里换一个名字")
        if personal:
            if inspected.name in settings.blocked:
                raise Invalid(f"空间管理员禁用了 {inspected.name}，不能安装")
            if any(row.plugin == inspected.name for row in await self._rows(workspace_id, "")):
                raise Invalid(f"空间里已经有 {inspected.name}，不能自己再装一份；要用请联系空间管理员授权")
        await self._blobs.put(data)
        return await self._save(actor, workspace_id, actor if personal else "", inspected, wanted)

    async def _save(self, actor: str, workspace_id: str, owner: str, inspected: InspectedArchive,
                    targets: list[str], *, checked: PluginInstallation | None = None) -> Installed:
        """同一个属主下同名的再传一次是升级：换版本与压缩包，档位、给谁用、个人开关不动。
        开了专属宿主时记成检查中、同一事务里排试装；checked 是同一份包已经试装过的那一行（转成空间插件时），结果照搬。"""
        if checked is not None and checked.status != STATUS_CHECKING:
            trial = {"tool_names": list(checked.tool_names), "status": checked.status,
                     "status_reason": checked.status_reason}
        else:
            trial = {"tool_names": [], "status": STATUS_CHECKING if self.cloud else STATUS_READY, "status_reason": ""}
        values = {"version": inspected.version, "description": inspected.description, "title": inspected.title,
                  "detail": inspected.detail, "components": [asdict(item) for item in inspected.components],
                  "archive_digest": inspected.digest, "archive_bytes": inspected.size, "targets": targets,
                  "capabilities": list(inspected.capabilities), **trial, "updated_by": actor,
                  "updated_at": func.now()}
        with in_workspace(workspace_id):
            async with self._sessions.begin() as session:
                plugin_id = await session.scalar(
                    insert(PluginInstallation)
                    .values(id=f"pi_{uuid.uuid4().hex}", workspace_id=workspace_id, owner_user_id=owner,
                            plugin=inspected.name, source=SOURCE_UPLOAD,
                            mode=MODE_OPTIONAL if owner else MODE_OFF, narrowed=False, **values)
                    .on_conflict_do_update(constraint="uq_plugin_installation", set_=values)
                    .returning(PluginInstallation.id))
                if trial["status"] == STATUS_CHECKING:
                    await enqueue(session, TRIAL_KIND, workspace_id=workspace_id, subject_id=plugin_id,
                                  payload={"digest": inspected.digest})
                if owner:  # 自己装的默认开着；升级时不动他原来的开关
                    await session.execute(
                        insert(PluginSwitch).values(workspace_id=workspace_id, user_id=owner, plugin=inspected.name,
                                                    enabled=True).on_conflict_do_nothing())
        return _view(await self._row(workspace_id, plugin_id))

    async def remove(self, actor: str, workspace_id: str, plugin_id: str) -> Installed:
        """管理员删空间插件。压缩包留在对象存储里（按摘要共用，回收以后做）。"""
        self._require_admin(actor, workspace_id)
        row = await self._row(workspace_id, plugin_id)
        if row.owner_user_id:
            raise Invalid("个人插件由本人管理；不想让它生效请把包名加进禁用清单")
        if row.narrowed:
            await self._access.set_users(actor, workspace_id, f"{RESOURCE_TYPE}:{row.id}", ())
        return await self._delete(workspace_id, row)

    async def remove_own(self, actor: str, workspace_id: str, plugin_id: str) -> Installed:
        """本人删自己装的个人插件。"""
        self._require_member(actor, workspace_id)
        row = await self._row(workspace_id, plugin_id)
        if row.owner_user_id != actor:
            raise NotFound()
        return await self._delete(workspace_id, row)

    async def _delete(self, workspace_id: str, row: PluginInstallation) -> Installed:
        plugin_id = row.id
        with in_workspace(workspace_id):
            async with self._sessions.begin() as session:
                await session.execute(delete(PluginInstallation).where(PluginInstallation.id == plugin_id))
                switches = delete(PluginSwitch).where(PluginSwitch.workspace_id == workspace_id,
                                                      PluginSwitch.plugin == row.plugin)
                if row.owner_user_id:
                    await session.execute(switches.where(PluginSwitch.user_id == row.owner_user_id))
                else:
                    # 删空间插件：各人对它的开关一起清掉；自己另装了同名个人插件的人，开关是那份个人插件的，留着
                    owners = select(PluginInstallation.owner_user_id).where(
                        PluginInstallation.workspace_id == workspace_id, PluginInstallation.plugin == row.plugin,
                        PluginInstallation.owner_user_id != "")
                    await session.execute(switches.where(PluginSwitch.user_id.not_in(owners)))
        return _view(row)

    # ---- 成员 ----

    async def _visible(self, workspace_id: str, user_id: str) -> tuple[list[PluginInstallation], PluginSettings]:
        """这个人看得到的上传插件：给了他的空间插件（没关的）和他自己装的；同名时只留空间的。"""
        rows = await self._rows(workspace_id, "", user_id)
        if not rows:
            return [], PluginSettings()
        shared = [row for row in rows if not row.owner_user_id and row.mode != MODE_OFF
                  and self._granted(user_id, workspace_id, row)]
        taken = {row.plugin for row in rows if not row.owner_user_id}
        own = [row for row in rows if row.owner_user_id == user_id and row.plugin not in taken]
        return [*shared, *own], await self.settings(workspace_id)

    async def mine(self, actor: str, workspace_id: str) -> tuple[Mine, ...]:
        self._require_member(actor, workspace_id)
        rows, settings = await self._visible(workspace_id, actor)
        switches = await self._switches(workspace_id, actor) if rows else {}
        return tuple(self._mine(row, switches, settings) for row in rows)

    @staticmethod
    def _mine(row: PluginInstallation, switches: dict[str, bool], settings: PluginSettings) -> Mine:
        locked = not row.owner_user_id and row.mode == MODE_ON
        return Mine(id=row.id, name=row.plugin, version=row.version, description=row.description,
                    scope=SCOPE_PERSONAL if row.owner_user_id else SCOPE_WORKSPACE, targets=tuple(row.targets),
                    capabilities=tuple(row.capabilities), status=row.status, reason=row.status_reason,
                    enabled=locked or switches.get(row.plugin, False), locked=locked,
                    blocked=bool(row.owner_user_id) and (row.plugin in settings.blocked or not settings.allow_personal),
                    submitted=row.submitted_at is not None, title=row.title,
                    components=tuple(row.components or ()), tools=tuple(row.tool_names))

    async def detail(self, actor: str, workspace_id: str, plugin_id: str, *, as_admin: bool) -> str:
        """说明文档（上传时从包里读的 Markdown）。管理员（插件管理页）看空间里的任何一个；成员看「我的插件」里有的。"""
        if as_admin:
            self._require_admin(actor, workspace_id)
            return (await self._row(workspace_id, plugin_id)).detail
        self._require_member(actor, workspace_id)
        rows, _ = await self._visible(workspace_id, actor)
        row = next((item for item in rows if item.id == plugin_id), None)
        if row is None:
            raise NotFound()
        return row.detail

    async def switch(self, actor: str, workspace_id: str, plugin_id: str, enabled: bool) -> Mine:
        self._require_member(actor, workspace_id)
        rows, settings = await self._visible(workspace_id, actor)
        row = next((item for item in rows if item.id == plugin_id), None)
        if row is None:
            raise NotFound()
        if not row.owner_user_id and row.mode == MODE_ON:
            raise Invalid("这个插件由管理员设为默认启用，不能自己关")
        with in_workspace(workspace_id):
            async with self._sessions.begin() as session:
                await session.execute(
                    insert(PluginSwitch).values(workspace_id=workspace_id, user_id=actor, plugin=row.plugin,
                                                enabled=enabled)
                    .on_conflict_do_update(index_elements=["workspace_id", "user_id", "plugin"],
                                           set_={"enabled": enabled, "updated_at": func.now()}))
        return self._mine(row, {row.plugin: enabled}, settings)

    # ---- 提交给管理员：个人插件 → 空间插件 ----

    async def submit(self, actor: str, workspace_id: str, plugin_id: str) -> Mine:
        """本人把自己装的插件提交给空间管理员。管理员在「成员安装的」里看到，决定要不要转成空间插件。"""
        self._require_member(actor, workspace_id)
        row = await self._row(workspace_id, plugin_id)
        if row.owner_user_id != actor:
            raise NotFound()
        await self._write(workspace_id, plugin_id, {"submitted_at": func.now()})
        fresh = await self._row(workspace_id, plugin_id)
        return self._mine(fresh, await self._switches(workspace_id, actor), await self.settings(workspace_id))

    async def adopt(self, actor: str, workspace_id: str, plugin_id: str) -> Installed:
        """管理员把成员的个人插件转成空间插件（同一份压缩包）：转完默认关闭，由管理员再定档和给谁用。
        原来那份个人插件留着，但同名时空间的优先，它不再单独生效。"""
        self._require_admin(actor, workspace_id)
        row = await self._row(workspace_id, plugin_id)
        if not row.owner_user_id or not row.archive_digest:
            raise Invalid("只能把成员安装的个人插件转成空间插件")
        if any(item.plugin == row.plugin for item in await self._rows(workspace_id, "")):
            raise Invalid(f"空间里已经有 {row.plugin}；要换版本请在空间插件里重新上传")
        inspected = InspectedArchive(name=row.plugin, version=row.version, description=row.description,
                                     digest=row.archive_digest, size=row.archive_bytes,
                                     capabilities=tuple(row.capabilities), title=row.title, detail=row.detail,
                                     components=tuple(Component(**item) for item in row.components or ()))
        adopted = await self._save(actor, workspace_id, "", inspected, list(row.targets), checked=row)
        await self._write(workspace_id, plugin_id, {"submitted_at": None})
        return adopted

    # ---- 给宿主 ----

    async def effective(self, workspace_id: str, user_id: str, target: str) -> tuple[EffectiveUpload, ...]:
        """这个人此刻在 target（cloud 云端对话、device 本机对话）上生效的上传插件，按包名排序。
        空间里一个上传的插件都没有时只查一次库。"""
        rows, settings = await self._visible(workspace_id, user_id)
        rows = [row for row in rows if row.status == STATUS_READY and target in row.targets and row.archive_digest]
        if not rows:
            return ()
        switches = await self._switches(workspace_id, user_id)
        chosen = []
        for row in rows:
            if row.owner_user_id:
                allowed = (settings.allow_personal and row.plugin not in settings.blocked
                           and (target != TARGET_DEVICE or settings.allow_personal_device))
                on = allowed and switches.get(row.plugin, False)
            else:
                on = row.mode == MODE_ON or switches.get(row.plugin, False)
            if on:
                chosen.append(EffectiveUpload(id=row.id, name=row.plugin, version=row.version,
                                              digest=row.archive_digest or "", size=row.archive_bytes))
        return tuple(sorted(chosen, key=lambda item: item.name))

    async def effective_for(self, actor: str, workspace_id: str, target: str) -> tuple[EffectiveUpload, ...]:
        """接口用：先核对是成员。"""
        self._require_member(actor, workspace_id)
        return await self.effective(workspace_id, actor, target)

    async def blob(self, digest: str) -> bytes:
        """按摘要取压缩包（专属宿主装插件用；调用方已经按 effective 算过这个人该有哪些）。"""
        return await self._blobs.get(digest, max_bytes=MAX_ARCHIVE_BYTES)

    async def archive(self, actor: str, workspace_id: str, plugin_id: str) -> tuple[Installed, str, bytes]:
        """下载压缩包：管理员，或这个插件对他生效（任一种对话）的人。桌面端同步用。返回（插件, 摘要, 内容）。"""
        self._require_member(actor, workspace_id)
        row = await self._row(workspace_id, plugin_id)
        allowed = self._admin(actor, workspace_id)
        for target in TARGETS:
            allowed = allowed or any(item.id == plugin_id for item in await self.effective(workspace_id, actor, target))
        if not allowed or not row.archive_digest:
            raise NotFound()
        return _view(row), row.archive_digest, await self._blobs.get(row.archive_digest, max_bytes=MAX_ARCHIVE_BYTES)
