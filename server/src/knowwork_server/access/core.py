"""服务的公共部分：事务（设置行级安全变量、收集规则变更、提交后回放）、调用者授权、判定。"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from . import store
from .engine import NOTHING_VISIBLE, PolicyEngine, VisibleSet
from .errors import AccessDenied, NotFound
from .models import WorkspaceMember
from .rls import set_workspace
from .rules import Rule, RuleChange
from .vocab import WORKSPACE_RESOURCE, Action, InvalidInput, Resource, parse_action, parse_resource_type, validate_id

log = logging.getLogger(__name__)


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

    @property
    def engine(self) -> PolicyEngine:
        return self._engine

    async def reload(self) -> bool:
        """从数据库整体重建判定器。失败时判定器保持不可用（全部拒绝）。"""
        try:
            self._engine.replace(await store.load_all(self._sessionmaker))
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
            obj = resource if resource == WORKSPACE_RESOURCE else Resource.parse(resource, allow_wildcard=False).key
        except InvalidInput:
            return False
        return self._engine.check(user_id, workspace_id, obj, act)

    def visible_resources(self, user_id: str, workspace_id: str, resource_type: str, action: str) -> VisibleSet:
        """用户在空间内能以某档权限看到的某类资源。all_ 为真表示全部可见。"""
        try:
            validate_id(user_id, "user_id")
            validate_id(workspace_id, "workspace_id")
            rtype = parse_resource_type(resource_type)
            act = parse_action(action)
        except InvalidInput:
            return NOTHING_VISIBLE
        return self._engine.visible(user_id, workspace_id, rtype, act)
