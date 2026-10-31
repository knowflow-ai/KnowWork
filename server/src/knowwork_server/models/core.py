"""服务公共部分：授权判定协议、事务（设置行级安全变量）、行到视图的转换。

厂商与模型是全平台共用的：不管调用者在哪个空间，读写都落在平台配置所在的空间（PLATFORM_WORKSPACE_ID），
管理只给系统管理员；所有空间的成员都能用。用量仍记在调用者自己的空间（recorder）。
"""

import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Protocol

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..access.members import PLATFORM_WORKSPACE_ID
from ..access.rls import set_workspace
from ..access.vocab import WORKSPACE_RESOURCE, InvalidInput, validate_id
from .crypto import SecretBox
from .errors import Conflict, Denied, Invalid
from .schemas import ModelView, ProviderView
from .tables import Model, ModelProvider

ADMIN_ACTION = "edit"  # 工作空间 edit = 空间 owner 与 admin（看本空间用量）


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
    def __init__(self, sessionmaker: async_sessionmaker, authorizer: Authorizer, box: SecretBox) -> None:
        self._sessionmaker = sessionmaker
        self._auth = authorizer
        self._box = box

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


def provider_view(row: ModelProvider) -> ProviderView:
    return ProviderView(id=row.id, name=row.name, kind=row.kind, base_url=row.base_url,
                        api_key_configured=bool(row.api_key_ciphertext), api_key_last4=row.api_key_last4 or "",
                        enabled=row.enabled)


def model_view(row: Model) -> ModelView:
    return ModelView(id=row.id, provider_id=row.provider_id, alias=row.alias, name=row.name,
                     capabilities=tuple(row.capabilities or ()), context_length=row.context_length,
                     input_price_per_mtok=row.input_price_per_mtok, output_price_per_mtok=row.output_price_per_mtok,
                     currency=row.currency, enabled=row.enabled, thinking=row.thinking,
                     max_output_tokens=row.max_output_tokens)

