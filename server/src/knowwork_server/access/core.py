"""服务的公共部分：事务（设置行级安全变量、收集规则变更、提交后回放）、调用者授权、判定。"""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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


class AccessCore:
    def __init__(self, sessionmaker: async_sessionmaker, engine: PolicyEngine | None = None) -> None:
        self._sessionmaker = sessionmaker
        self._engine = engine or PolicyEngine()
        self._hierarchy: ResourceHierarchy = NO_HIERARCHY
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
        """可授权、可判定的资源类型：内置的加上资源层级引入的。"""
        return RESOURCE_TYPES | self._hierarchy.resource_types

    def use_hierarchy(self, hierarchy: ResourceHierarchy) -> None:
        """启动时装上资源层级（只有一个）。它引入的类型不能和内置类型重名。"""
        types = frozenset(hierarchy.resource_types)
        bad = [t for t in types if t in RESOURCE_TYPES or t == WORKSPACE_RESOURCE or not TYPE_PATTERN.fullmatch(t)]
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
        await self._after_commit(tx.change)

    @asynccontextmanager
    async def _read(self, workspace_id: str) -> AsyncIterator[AsyncSession]:
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            yield session

    async def _after_commit(self, change: RuleChange) -> None:
        try:
            self._engine.apply(change)
        except Exception:
            log.exception("applying policy change failed; reloading")
            await self.reload()

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
