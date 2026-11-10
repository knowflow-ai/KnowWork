"""服务公共部分：授权判定协议、事务（设置行级安全变量）、行到视图的转换。

厂商与模型是全平台共用的：不管调用者在哪个空间，读写都落在平台配置所在的空间（PLATFORM_WORKSPACE_ID），
管理只给系统管理员；所有空间的成员都能用。用量仍记在调用者自己的空间（recorder）。
按别名取模型读的是进程内快照（cache.py）：改厂商或模型走 _write，提交后作废；同一事务里 NOTIFY kw_models
（随提交送达、回滚不发），别的进程（独立的模型网关）用 follow_changes 跟着作废。
"""

import asyncio
import secrets
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from ..access.members import PLATFORM_WORKSPACE_ID
from ..access.rls import set_workspace
from ..access.vocab import WORKSPACE_RESOURCE, InvalidInput, validate_id
from ..db.listen import listen
from .cache import DEFAULT_TTL_SECONDS, CatalogSnapshot, SnapshotCache, load_snapshot
from .crypto import SecretBox
from .errors import Conflict, Denied, Invalid
from .schemas import ModelView, ProviderView
from .tables import Model, ModelProvider

ADMIN_ACTION = "edit"  # 工作空间 edit = 空间 owner 与 admin（看本空间用量）
MODELS_CHANNEL = "kw_models"


class Authorizer(Protocol):
    """access.AccessService 满足这个协议。"""

    def check(self, user_id: str, workspace_id: str, resource: str, action: str) -> bool: ...

    def is_system_admin(self, user_id: str) -> bool: ...


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(10)}"


def check_id(value: str, label: str) -> str:
    try:
        return validate_id(value, label)
    except InvalidInput as error:
        raise Invalid(str(error)) from error


class CatalogCore:
    def __init__(self, sessionmaker: async_sessionmaker, authorizer: Authorizer, box: SecretBox, *,
                 cache_ttl: float = DEFAULT_TTL_SECONDS) -> None:
        self._sessionmaker = sessionmaker
        self._auth = authorizer
        self._box = box
        self._snapshot = SnapshotCache(self._load_snapshot, ttl=cache_ttl)
        self._listeners: tuple[Callable[[], None], ...] = ()

    def on_change(self, listener: Callable[[], None]) -> None:
        """本进程改了厂商或模型（提交后）同步回调，必须立即返回（例如把可选模型推给宿主，见 models/host_sync.py）。"""
        self._listeners = (*self._listeners, listener)

    def _require_admin(self, actor_id: str) -> None:
        if not self._auth.is_system_admin(actor_id):
            raise Denied()

    def is_member(self, user_id: str, workspace_id: str) -> bool:
        return self._auth.check(user_id, workspace_id, WORKSPACE_RESOURCE, "use")

    @asynccontextmanager
    async def _tx(self) -> AsyncIterator[AsyncSession]:
        try:
            async with self._sessionmaker() as session, session.begin():
                await set_workspace(session, PLATFORM_WORKSPACE_ID)
                yield session
        except IntegrityError as error:
            raise Conflict("名称或别名已存在，或被其他对象引用") from error

    @asynccontextmanager
    async def _write(self) -> AsyncIterator[AsyncSession]:
        """改厂商或模型的事务：通知别的进程（随提交送达），结束后作废本进程的快照（失败了也作废，多读一次没坏处）。"""
        try:
            async with self._tx() as session:
                yield session
                await session.execute(text("SELECT pg_notify(:channel, '')"), {"channel": MODELS_CHANNEL})
        finally:
            self._snapshot.invalidate()
            for listener in self._listeners:
                listener()

    async def follow_changes(self, engine: AsyncEngine, stop: asyncio.Event, *,
                             ready: asyncio.Event | None = None) -> None:
        """跟着别的进程改的厂商与模型走：收到通知就作废快照，下次读重新加载。每次（重新）连上也作废一次，补上断开
        期间漏掉的通知。stop 置位后返回。独立的模型网关进程开着它；业务服务自己改自己作废，worker 不缓存，都不用开。"""
        await listen(engine, MODELS_CHANNEL, lambda _payload: self._snapshot.invalidate(), stop, ready=ready,
                     on_connected=self._snapshot.invalidate)

    async def _load_snapshot(self) -> CatalogSnapshot:
        async with self._tx() as session:
            return await load_snapshot(session)


def provider_view(row: ModelProvider) -> ProviderView:
    return ProviderView(id=row.id, name=row.name, kind=row.kind, base_url=row.base_url,
                        api_key_configured=bool(row.api_key_ciphertext), api_key_last4=row.api_key_last4 or "",
                        enabled=row.enabled)


def model_view(row: Model) -> ModelView:
    return ModelView(id=row.id, provider_id=row.provider_id, alias=row.alias, name=row.name,
                     capabilities=tuple(row.capabilities or ()), context_length=row.context_length,
                     input_price_per_mtok=row.input_price_per_mtok, output_price_per_mtok=row.output_price_per_mtok,
                     currency=row.currency, enabled=row.enabled, thinking=row.thinking, show_thinking=row.show_thinking,
                     max_output_tokens=row.max_output_tokens)

