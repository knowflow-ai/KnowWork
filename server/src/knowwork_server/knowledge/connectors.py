"""按一行知识源配置组装连接器，以及调用者身份的解析。"""

import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

import httpx
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..access.engine import VisibleSet
from ..access.rules import RuleChange
from ..identity.models import USER_ACTIVE, User
from .assertion import AssertionSigner
from .errors import IdentityUnavailable, SourceUnavailable
from .knowflow import AssertionIdentity, IdentityProvider, KnowFlowSource, SharedAccountIdentity
from .knowflow_mapping import AuthMode, KnowFlowConfig
from .models import KnowledgeSource
from .store import SecretKind, SourceRecord, SourceStore

log = logging.getLogger(__name__)

EmailLookup = Callable[[str], Awaitable[str | None]]
# 每种认证方式用的连接密钥
SECRET_OF: dict[str, SecretKind] = {"assertion": "service_key", "shared": "account_key"}


class AccessChecker(Protocol):
    """access.AccessService 满足这个协议。"""

    def check(self, user_id: str, workspace_id: str, resource: str, action: str) -> bool: ...

    def visible_resources(self, user_id: str, workspace_id: str, resource_type: str, action: str) -> VisibleSet: ...

    async def purge_grants(self, session: AsyncSession, workspace_id: str, *, resources: tuple[str, ...] = (),
                           prefixes: tuple[str, ...] = ()) -> RuleChange: ...

    async def settle(self, change: RuleChange) -> None: ...


def user_email_lookup(sessionmaker: async_sessionmaker) -> EmailLookup:
    """默认的邮箱解析：账号表里启用状态用户的邮箱，停用或不存在返回 None。user 是全局表，不受行级安全约束。"""

    async def lookup(user_id: str) -> str | None:
        async with sessionmaker() as session:
            row = await session.execute(select(User.email, User.status).where(User.id == user_id))
            found = row.first()
        if found is None or found.status != USER_ACTIVE:
            return None
        return found.email

    return lookup


def parse_config(record: SourceRecord) -> KnowFlowConfig:
    try:
        return KnowFlowConfig.model_validate(record.config)
    except ValidationError as error:
        log.error("knowledge source %s has invalid config", record.id)
        raise SourceUnavailable(f"知识源「{record.name}」配置有误，请联系管理员") from error


class ConnectorFactory:
    def __init__(self, store: SourceStore, client: httpx.AsyncClient) -> None:
        self._store = store
        self._client = client

    def build(self, record: SourceRecord) -> KnowledgeSource:
        if record.kind != "knowflow":
            raise SourceUnavailable(f"知识源「{record.name}」的类型暂不支持")
        config = parse_config(record)
        return KnowFlowSource(source_id=record.id, name=record.name, base_url=record.base_url, config=config,
                              identity=self._identity(record, config.auth_mode), client=self._client)

    def _identity(self, record: SourceRecord, mode: AuthMode) -> IdentityProvider:
        secret = self._store.open_secret(record, SECRET_OF[mode])
        if not secret:
            what = "服务密钥" if mode == "assertion" else "统一账号的 KnowFlow API Key"
            raise IdentityUnavailable(f"知识源「{record.name}」还没有配置{what}，请联系管理员")
        return AssertionIdentity(AssertionSigner(secret)) if mode == "assertion" else SharedAccountIdentity(secret)
