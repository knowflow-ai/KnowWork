"""knowledge_source 的读写。每个事务先设置行级安全变量，只看得到本空间的行。

密钥在这里加解密：入库前 seal，出库后只有连接器工厂调用 open_* 拿到明文。
知识源一行只存一个连接密钥，含义看认证方式：签名断言是服务密钥，统一账号是那个账号的 KnowFlow API Key。
两者加密时绑定的上下文不同（SecretKind），换了认证方式旧密文就解不开，不会把服务密钥当成 API Key 发出去。
"""

import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal, TypeVar

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..access.rls import set_workspace
from .crypto import SecretBox, SecretError, mask
from .errors import NotFound
from .tables import KnowledgeSourceRow


@dataclass(frozen=True)
class SourceRecord:
    """一行知识源的不可变快照；只带密文与末四位，不带明文。"""

    id: str
    workspace_id: str
    name: str
    kind: str
    base_url: str
    config: dict[str, Any]
    secret_ciphertext: str | None
    secret_last4: str
    enabled: bool
    created_by: str


SecretKind = Literal["service_key", "account_key"]
T = TypeVar("T")


@dataclass(frozen=True)
class SourceChanges:
    name: str | None = None
    base_url: str | None = None
    config: dict[str, Any] | None = None
    secret: str | None = None
    secret_kind: SecretKind = "service_key"
    enabled: bool | None = None


def _record(row: KnowledgeSourceRow) -> SourceRecord:
    return SourceRecord(id=row.id, workspace_id=row.workspace_id, name=row.name, kind=row.kind,
                        base_url=row.base_url, config=dict(row.config or {}), secret_ciphertext=row.secret_ciphertext,
                        secret_last4=row.secret_last4, enabled=row.enabled, created_by=row.created_by)


def secret_context(workspace_id: str, source_id: str, kind: SecretKind) -> str:
    return f"{workspace_id}/{source_id}/{kind}"


class SourceStore:
    def __init__(self, sessionmaker: async_sessionmaker, box: SecretBox) -> None:
        self._sessionmaker = sessionmaker
        self._box = box

    @asynccontextmanager
    async def _session(self, workspace_id: str) -> AsyncIterator[AsyncSession]:
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            yield session

    async def create(self, workspace_id: str, *, name: str, kind: str, base_url: str, config: dict[str, Any],
                     secret: str | None, secret_kind: SecretKind, created_by: str) -> SourceRecord:
        source_id = "ks_" + secrets.token_hex(8)
        row = KnowledgeSourceRow(id=source_id, workspace_id=workspace_id, name=name, kind=kind, base_url=base_url,
                                 config=config, created_by=created_by, enabled=True, secret_last4="")
        if secret:
            row.secret_ciphertext = self._box.seal(secret, context=secret_context(workspace_id, source_id, secret_kind))
            row.secret_last4 = mask(secret)
        async with self._session(workspace_id) as session:
            session.add(row)
        return _record(row)

    async def get(self, workspace_id: str, source_id: str) -> SourceRecord:
        async with self._session(workspace_id) as session:
            row = await session.get(KnowledgeSourceRow, source_id)
            if row is None:
                raise NotFound()
            return _record(row)

    async def list(self, workspace_id: str) -> tuple[SourceRecord, ...]:
        async with self._session(workspace_id) as session:
            rows = await session.scalars(select(KnowledgeSourceRow).order_by(KnowledgeSourceRow.created_at,
                                                                             KnowledgeSourceRow.id))
            return tuple(_record(row) for row in rows)

    async def update(self, workspace_id: str, source_id: str, changes: SourceChanges) -> SourceRecord:
        async with self._session(workspace_id) as session:
            row = await session.get(KnowledgeSourceRow, source_id, with_for_update=True)
            if row is None:
                raise NotFound()
            for attr in ("name", "base_url", "config", "enabled"):
                value = getattr(changes, attr)
                if value is not None:
                    setattr(row, attr, value)
            if changes.secret:
                row.secret_ciphertext = self._box.seal(
                    changes.secret, context=secret_context(workspace_id, source_id, changes.secret_kind))
                row.secret_last4 = mask(changes.secret)
            await session.flush()
            return _record(row)

    async def delete(self, workspace_id: str, source_id: str,
                     also: Callable[[AsyncSession], Awaitable[T]]) -> T:
        """删除知识源；also 在同一事务里执行（删掉指向它的授权），返回它的结果。"""
        async with self._session(workspace_id) as session:
            result = await session.execute(delete(KnowledgeSourceRow).where(KnowledgeSourceRow.id == source_id))
            if result.rowcount == 0:
                raise NotFound()
            return await also(session)

    def open_secret(self, record: SourceRecord, kind: SecretKind) -> str | None:
        if not record.secret_ciphertext:
            return None
        try:
            return self._box.open(record.secret_ciphertext,
                                  context=secret_context(record.workspace_id, record.id, kind))
        except SecretError:
            return None  # 主密钥换了、密文损坏或是另一种认证方式的密钥：当作未配置，调用方明确拒绝
