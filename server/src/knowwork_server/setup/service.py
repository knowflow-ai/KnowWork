"""初始化向导后端：首次部署时创建首个系统管理员与默认工作空间，并发下只成功一次，完成后永久关闭。

并发控制：先在独立短事务里 `INSERT ... ON CONFLICT DO NOTHING` 保证单例行存在，再在初始化事务里
`SELECT ... FOR UPDATE NOWAIT` 锁住它；建号与该行的完成标记在同一事务提交。锁被占用的请求立即被拒绝，
锁释放后再来的请求看到已完成同样被拒绝。

默认空间由 access 服务在它自己的事务里创建（需要设置行级安全变量并回放策略），此时初始化行仍被锁着。
若上次在两次提交之间崩溃，会留下默认空间却没有完成标记：此时一律拒绝，需运维人工处理，不猜测修复。
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..access.members import DEFAULT_WORKSPACE_ID
from ..access.models import Workspace
from ..access.service import AccessService
from ..identity.users import insert_user
from .models import SINGLETON_ID, SystemSetup


class SetupError(Exception):
    """初始化失败的基类。"""


class AlreadyInitialized(SetupError):
    pass


class SetupInProgress(SetupError):
    """另一个初始化请求正持有锁。立即拒绝而不排队，避免等锁的请求占满连接池、反把持锁方饿死。"""


class SetupInconsistent(SetupError):
    """数据处于半初始化状态，需人工处理。"""


@dataclass(frozen=True)
class SetupStatus:
    initialized: bool


@dataclass(frozen=True)
class SetupResult:
    admin_user_id: str
    workspace_id: str


LOCK_NOT_AVAILABLE = "55P03"


def _sqlstate(error: DBAPIError) -> str | None:
    return getattr(error.orig, "sqlstate", None) or getattr(error.orig, "pgcode", None)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class SetupService:
    def __init__(self, sessionmaker: async_sessionmaker, access: AccessService,
                 clock: Callable[[], datetime] = _utcnow) -> None:
        self._sessionmaker = sessionmaker
        self._access = access
        self._clock = clock

    async def status(self) -> SetupStatus:
        async with self._sessionmaker() as session:
            completed = await session.scalar(select(SystemSetup.completed_at).where(SystemSetup.id == SINGLETON_ID))
        return SetupStatus(initialized=completed is not None)

    async def is_system_admin(self, user_id: str) -> bool:
        async with self._sessionmaker() as session:
            row = await session.get(SystemSetup, SINGLETON_ID)
        return row is not None and row.completed_at is not None and row.admin_user_id == user_id

    async def initialize(self, *, email: str, name: str, password: str) -> SetupResult:
        if (await self.status()).initialized:  # 已完成时不去抢锁
            raise AlreadyInitialized()
        await self._ensure_row()
        async with self._sessionmaker() as session, session.begin():
            row = await self._lock(session)
            if row.completed_at is not None:
                raise AlreadyInitialized()
            await self._require_no_workspace()
            user = await insert_user(session, email=email, name=name, password=password)
            workspace_id = await self._access.ensure_default_workspace(user.id)
            row.completed_at = self._clock()
            row.admin_user_id = user.id
            row.default_workspace_id = workspace_id
        return SetupResult(admin_user_id=user.id, workspace_id=workspace_id)

    async def _ensure_row(self) -> None:
        """单例行在独立的短事务里建好，锁事务里就不会因等待别人未提交的插入而阻塞。"""
        async with self._sessionmaker() as session, session.begin():
            await session.execute(pg_insert(SystemSetup).values(id=SINGLETON_ID).on_conflict_do_nothing())

    async def _lock(self, session: AsyncSession) -> SystemSetup:
        stmt = select(SystemSetup).where(SystemSetup.id == SINGLETON_ID).with_for_update(nowait=True)
        try:
            result = await session.execute(stmt.execution_options(populate_existing=True))
        except DBAPIError as error:
            if _sqlstate(error) == LOCK_NOT_AVAILABLE:
                raise SetupInProgress() from error
            raise
        return result.scalar_one()

    async def _require_no_workspace(self) -> None:
        async with self._sessionmaker() as session:
            exists = await session.scalar(select(Workspace.id).where(Workspace.id == DEFAULT_WORKSPACE_ID))
        if exists is not None:
            raise SetupInconsistent()
