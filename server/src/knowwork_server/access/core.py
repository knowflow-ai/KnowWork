"""服务的公共部分：事务（设置行级安全变量、收集规则变更、提交后回放）、调用者授权、判定、跨进程刷新。

判定器是每个进程内存里的镜像。本进程写的规则提交后增量回放；别的进程（worker 里的组织同步、删知识库的任务，
多开的业务服务）写的，靠数据库通知：写规则的事务里 NOTIFY kw_policy（随提交送达、回滚就不发），
follow_changes 收到别的进程发的就整体重新加载。
"""

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from ..db.listen import listen
from . import store
from .engine import NOTHING_VISIBLE, PolicyEngine, VisibleSet
from .errors import AccessDenied, NotFound
from .hierarchy import NO_HIERARCHY, ResourceHierarchy
from .models import WorkspaceMember
from .rls import set_workspace
from .rules import Rule, RuleChange
from .vocab import (
    RESOURCE_TYPES,
    TYPE_PATTERN,
    WORKSPACE_RESOURCE,
    Action,
    InvalidInput,
    Resource,
    parse_action,
    parse_resource_type,
    validate_id,
)

log = logging.getLogger(__name__)

# 移出成员时扩展清理这个人在本空间的数据：(会话, 空间, 用户)，在移出的同一个事务里调用（会话已声明这个空间）
MemberRemoved = Callable[[AsyncSession, str, str], Awaitable[None]]
POLICY_CHANNEL = "kw_policy"
# 一次改动超过这么多条规则就整体重载，不逐条回放（逐条加 p 规则每条都要扫一遍，上万条时比整体重载慢得多）
APPLY_LIMIT = 2000
# 收到通知后等这么久再重载，把一阵连发的通知（一次同步改好几个空间）合成一次
RELOAD_DEBOUNCE_SECONDS = 0.2
# 跟着别的进程重新加载失败（数据库抖了一下）时隔这么久再试：失败后判定器是「全部拒绝」，不能等到下一次有人改权限
RELOAD_RETRY_SECONDS = 5.0


async def notify_policy_change(session: AsyncSession, origin: str) -> None:
    """在写规则的事务里调用：提交时通知别的进程重新加载（回滚则不发）。"""
    await session.execute(text("SELECT pg_notify(:channel, :origin)"), {"channel": POLICY_CHANNEL, "origin": origin})


class Tx:
    """一个写事务。规则的增删与业务表写入在同一个数据库事务里。"""

    def __init__(self, session: AsyncSession, workspace_id: str) -> None:
        self.session = session
        self.workspace_id = workspace_id
        self.change = RuleChange()

    async def add_rules(self, *rules: Rule) -> None:
        await store.insert_rules(self.session, rules)
        self.change = self.change.merge(RuleChange(added=rules))

    async def remove_rules(self, *rules: Rule) -> None:
        await store.delete_rules(self.session, rules)
        self.change = self.change.merge(RuleChange(removed=rules))


class MultiTx:
    """跨多个空间的一个写事务（企业组织同步：组织表是全系统的，规则要写进每个空间）。
    workspace() 切到某个空间再写它的规则（行级安全按当前空间判），各空间依次处理，切走后不再用前一个的 Tx。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._txs: list[Tx] = []

    async def workspace(self, workspace_id: str) -> Tx:
        await set_workspace(self.session, workspace_id)
        tx = Tx(self.session, workspace_id)
        self._txs.append(tx)
        return tx

    @property
    def change(self) -> RuleChange:
        merged = RuleChange()
        for tx in self._txs:
            merged = merged.merge(tx.change)
        return merged


class AccessCore:
    def __init__(self, sessionmaker: async_sessionmaker, engine: PolicyEngine | None = None, *,
                 mirror: bool = True) -> None:
        """mirror=False：只写库、不在内存里维护判定器（worker：它不判定，写完由通知让业务服务重新加载）。"""
        self._sessionmaker = sessionmaker
        self._engine = engine or PolicyEngine()
        self._mirror = mirror
        self._origin = uuid.uuid4().hex  # 本进程发的通知自己不用重载
        self._hierarchy: ResourceHierarchy = NO_HIERARCHY
        self._extension_types: frozenset[str] = frozenset()  # 扩展登记的资源类型（平铺，没有上级）
        # 系统管理员（初始化向导建的那一个账号）：在任何空间里都按所有者处理，不必是成员。
        # 由应用在启动时、初始化完成时告诉这里（权限模块不去查初始化记录，免得和 setup 互相依赖）
        self._system_admin: str | None = None
        # 关闭了的空间：规则不加载，判定一律拒绝（系统管理员也一样）。整体重载时从空间表读
        self._closed: frozenset[str] = frozenset()
        self._member_removed: tuple[MemberRemoved, ...] = ()

    @property
    def engine(self) -> PolicyEngine:
        return self._engine

    @property
    def resource_types(self) -> frozenset[str]:
        """可授权、可判定的资源类型：内置的，加上资源层级引入的、扩展登记的。"""
        return RESOURCE_TYPES | self._hierarchy.resource_types | self._extension_types

    @property
    def added_types(self) -> frozenset[str]:
        """不是内置的那些（资源层级引入的、扩展登记的）：都可以经授权接口授出。"""
        return self._hierarchy.resource_types | self._extension_types

    def add_resource_types(self, types: frozenset[str]) -> None:
        """启动时登记扩展自己的资源类型（例如数据源）：平铺、没有上级，登记后可以授权、判定、给默认的「给谁用」。
        谁对它有什么权限全靠授权；空间管理员的放行由扩展判定时自己加（不改已建空间的默认规则）。"""
        taken = RESOURCE_TYPES | self._hierarchy.resource_types | {WORKSPACE_RESOURCE}
        bad = [t for t in types if t in taken or not TYPE_PATTERN.fullmatch(t)]
        if bad:
            raise ValueError(f"extension declares invalid resource types: {bad}")
        self._extension_types = self._extension_types | frozenset(types)

    def use_hierarchy(self, hierarchy: ResourceHierarchy) -> None:
        """启动时装上资源层级（只有一个）。它引入的类型不能和内置类型重名。"""
        types = frozenset(hierarchy.resource_types)
        bad = [t for t in types if t in RESOURCE_TYPES or t in self._extension_types or t == WORKSPACE_RESOURCE
               or not TYPE_PATTERN.fullmatch(t)]
        if bad:
            raise ValueError(f"resource hierarchy declares invalid types: {bad}")
        if self._hierarchy is not NO_HIERARCHY:
            raise ValueError("a resource hierarchy is already installed")
        self._hierarchy = hierarchy

    def on_member_removed(self, hook: MemberRemoved) -> None:
        """启动时登记：成员被移出空间时在同一个事务里调用，例如扩展删掉他在这个空间的长期记忆。
        抛错时整个移出回滚，不留下人走了、数据还在的情况。"""
        self._member_removed = (*self._member_removed, hook)

    def set_system_admin(self, user_id: str | None) -> None:
        self._system_admin = user_id or None

    def is_system_admin(self, user_id: str) -> bool:
        return self._system_admin is not None and user_id == self._system_admin

    def is_open(self, workspace_id: str) -> bool:
        """空间没被关闭（软删除）。不查空间存不存在。"""
        return workspace_id not in self._closed

    async def reload(self) -> bool:
        """从数据库整体重建判定器。失败时判定器保持不可用（全部拒绝）。"""
        try:
            closed = await store.closed_workspaces(self._sessionmaker)
            self._engine.replace(await store.load_all(self._sessionmaker))
            self._closed = closed
            return True
        except Exception:
            self._engine.invalidate()
            log.exception("policy reload failed; access checks will deny until next successful reload")
            return False

    @asynccontextmanager
    async def _tx(self, workspace_id: str) -> AsyncIterator[Tx]:
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            tx = Tx(session, workspace_id)
            yield tx
            if not tx.change.empty:
                await notify_policy_change(session, self._origin)
        await self._after_commit(tx.change)

    @asynccontextmanager
    async def _tx_all(self) -> AsyncIterator[MultiTx]:
        async with self._sessionmaker() as session, session.begin():
            multi = MultiTx(session)
            yield multi
            if not multi.change.empty:
                await notify_policy_change(session, self._origin)
        await self._after_commit(multi.change)

    @asynccontextmanager
    async def _read(self, workspace_id: str) -> AsyncIterator[AsyncSession]:
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            yield session

    async def _after_commit(self, change: RuleChange) -> None:
        if change.empty or not self._mirror:
            return
        if len(change.added) + len(change.removed) > APPLY_LIMIT:
            await self.reload()
            return
        try:
            self._engine.apply(change)
        except Exception:
            log.exception("applying policy change failed; reloading")
            await self.reload()

    async def follow_changes(self, engine: AsyncEngine, stop: asyncio.Event, *,
                             ready: asyncio.Event | None = None, retry_seconds: float = RELOAD_RETRY_SECONDS) -> None:
        """跟着别的进程改的规则走：收到它们的通知就整体重新加载（合并一阵连发的）。每次（重新）连上也加载一次，
        补上断开期间漏掉的通知；加载失败隔几秒再试，直到成功。stop 置位后返回。业务服务进程启动时开着它；
        worker 不判定，不用开。"""
        pending = asyncio.Event()

        def on_message(origin: str) -> None:
            if origin != self._origin:
                pending.set()

        async def reloader() -> None:
            while not stop.is_set():
                await pending.wait()
                await asyncio.sleep(RELOAD_DEBOUNCE_SECONDS)
                pending.clear()
                if not await self.reload():
                    await asyncio.sleep(retry_seconds)
                    pending.set()

        task = asyncio.create_task(reloader())
        try:
            await listen(engine, POLICY_CHANNEL, on_message, stop, ready=ready, on_connected=pending.set)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def _require(self, actor_id: str, workspace_id: str, action: Action) -> None:
        if workspace_id in self._closed:
            raise AccessDenied()
        if self.is_system_admin(actor_id) and self._engine.healthy:
            return
        if not self._engine.check(actor_id, workspace_id, WORKSPACE_RESOURCE, action):
            raise AccessDenied()

    async def _member(self, session: AsyncSession, workspace_id: str, user_id: str,
                      *, lock: bool = False) -> WorkspaceMember:
        stmt = select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace_id,
                                             WorkspaceMember.user_id == user_id)
        if lock:
            stmt = stmt.with_for_update()
        member = await session.scalar(stmt)
        if member is None:
            raise NotFound("该用户不是本空间成员")
        return member

    # ---- 判定 ----

    def check(self, user_id: str, workspace_id: str, resource: str, action: str) -> bool:
        """用户在空间内对资源是否有某档权限。输入不合法或判定出错一律为 False。"""
        try:
            validate_id(user_id, "user_id")
            validate_id(workspace_id, "workspace_id")
            act = parse_action(action)
            obj = resource if resource == WORKSPACE_RESOURCE else Resource.parse(
                resource, allow_wildcard=False, types=self.resource_types).key
        except InvalidInput:
            return False
        if workspace_id in self._closed:
            return False
        if self.is_system_admin(user_id) and self._engine.healthy:
            return True
        if self._engine.check(user_id, workspace_id, obj, act):
            return True
        return obj != WORKSPACE_RESOURCE and any(
            self._engine.check(user_id, workspace_id, upper, act) for upper in self._ancestors(workspace_id, obj))

    def _ancestors(self, workspace_id: str, obj: str) -> tuple[str, ...]:
        try:
            return tuple(self._hierarchy.ancestors(workspace_id, obj))
        except Exception:
            log.exception("resource hierarchy failed for %s; denying", obj)
            return ()

    def subjects_of(self, user_id: str, workspace_id: str) -> frozenset[str]:
        """这个人在空间里属于哪些主体（`user:`、`role:`、`dept:` 含上级、`group:`、`org:`），给扩展按主体写的规则判定用
        （例如问数表上的行列规则：「华东销售部 → 区域 = 华东」）。输入不合法或空间关了是空集。"""
        try:
            validate_id(user_id, "user_id")
            validate_id(workspace_id, "workspace_id")
        except InvalidInput:
            return frozenset()
        if workspace_id in self._closed:
            return frozenset()
        return self._engine.subjects(user_id, workspace_id)

    def visible_resources(self, user_id: str, workspace_id: str, resource_type: str, action: str) -> VisibleSet:
        """用户在空间内能以某档权限看到的某类资源。all_ 为真表示全部可见。"""
        try:
            validate_id(user_id, "user_id")
            validate_id(workspace_id, "workspace_id")
            rtype = parse_resource_type(resource_type, self.resource_types)
            act = parse_action(action)
        except InvalidInput:
            return NOTHING_VISIBLE
        if workspace_id in self._closed:
            return NOTHING_VISIBLE
        if self.is_system_admin(user_id) and self._engine.healthy:
            return VisibleSet(all_=True, ids=frozenset())
        direct = self._engine.visible(user_id, workspace_id, rtype, act)
        if direct.all_ or not self._hierarchy.resource_types:
            return direct
        return VisibleSet(all_=False, ids=direct.ids | self._visible_below(user_id, workspace_id, rtype, act))

    def _visible_below(self, user_id: str, workspace_id: str, rtype: str, act: Action) -> frozenset[str]:
        """经上级授权看得到的资源：每种上级类型取出有权的上级，再问层级它们下面有哪些。"""
        found: set[str] = set()
        try:
            for upper_type in self._hierarchy.resource_types:
                granted = self._engine.visible(user_id, workspace_id, upper_type, act)
                if granted.all_:
                    found |= self._hierarchy.descendants(workspace_id, None, rtype)
                elif granted.ids:
                    keys = frozenset(f"{upper_type}:{ident}" for ident in granted.ids)
                    found |= self._hierarchy.descendants(workspace_id, keys, rtype)
        except Exception:
            log.exception("resource hierarchy failed while listing %s; using direct grants only", rtype)
            return frozenset()
        return frozenset(found)
