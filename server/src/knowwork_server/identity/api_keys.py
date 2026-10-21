"""个人 API Key（决策 K7）：`ak_<8 位前缀>_<随机串>`，库里只存前缀与整串的 SHA-256，明文只在创建时返回一次。"""

import re
import secrets
import string
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..auth.principal import Principal
from .credentials import Clock, digest, new_id, new_token, same, utcnow
from .errors import InvalidInput
from .models import USER_ACTIVE, ApiKey, User

_KEY = re.compile(r"^ak_([a-z0-9]{8})_([A-Za-z0-9_-]{32,64})$")
_SCOPE = re.compile(r"^[a-z][a-z0-9_.-]{0,30}:[a-z][a-z0-9_.-]{0,30}$")
_PREFIX_ALPHABET = string.ascii_lowercase + string.digits
MAX_SCOPES = 32
MAX_KEYS_PER_USER = 50


@dataclass(frozen=True)
class ApiKeyInfo:
    id: str
    name: str
    prefix: str
    scopes: tuple[str, ...]
    workspace_id: str | None
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None


@dataclass(frozen=True)
class IssuedApiKey:
    info: ApiKeyInfo
    plaintext: str


def _info(key: ApiKey) -> ApiKeyInfo:
    return ApiKeyInfo(id=key.id, name=key.name, prefix=key.prefix, scopes=tuple(key.scopes),
                      workspace_id=key.workspace_id, created_at=key.created_at, expires_at=key.expires_at,
                      last_used_at=key.last_used_at, revoked_at=key.revoked_at)


def validate_scopes(scopes: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    unique = tuple(dict.fromkeys(scopes))
    if not unique or len(unique) > MAX_SCOPES or not all(_SCOPE.match(scope) for scope in unique):
        raise InvalidInput("权限范围格式不正确")
    return unique


def _new_prefix() -> str:
    return "".join(secrets.choice(_PREFIX_ALPHABET) for _ in range(8))


class ApiKeyService:
    def __init__(self, sessionmaker: async_sessionmaker, *, touch_interval, clock: Clock = utcnow) -> None:
        self._sessionmaker = sessionmaker
        self._touch_interval = touch_interval
        self._clock = clock

    async def create(self, *, user_id: str, name: str, scopes: list[str], expires_at: datetime | None = None,
                     workspace_id: str | None = None) -> IssuedApiKey:
        clean_scopes = validate_scopes(scopes)
        now = self._clock()
        if expires_at is not None and expires_at <= now:
            raise InvalidInput("过期时间必须晚于现在")
        if len(await self.list(user_id)) >= MAX_KEYS_PER_USER:
            raise InvalidInput("API Key 数量已达上限")
        for _ in range(3):  # 前缀撞车概率极低，撞了换一个
            prefix = _new_prefix()
            plaintext = f"ak_{prefix}_{new_token(32)}"
            key = ApiKey(id=new_id(), user_id=user_id, name=name.strip()[:100], prefix=prefix,
                         secret_hash=digest(plaintext), scopes=list(clean_scopes), workspace_id=workspace_id,
                         created_at=now, expires_at=expires_at)
            try:
                async with self._sessionmaker() as session, session.begin():
                    session.add(key)
            except IntegrityError:
                continue
            return IssuedApiKey(info=_info(key), plaintext=plaintext)
        raise RuntimeError("无法生成唯一的 API Key 前缀")

    async def list(self, user_id: str) -> list[ApiKeyInfo]:
        async with self._sessionmaker() as session:
            rows = await session.execute(select(ApiKey).where(ApiKey.user_id == user_id)
                                         .order_by(ApiKey.created_at.desc()))
            return [_info(key) for key in rows.scalars()]

    async def revoke(self, *, user_id: str, key_id: str) -> bool:
        async with self._sessionmaker() as session, session.begin():
            result = await session.execute(
                update(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == user_id, ApiKey.revoked_at.is_(None))
                .values(revoked_at=self._clock()))
            return result.rowcount > 0

    async def resolve(self, presented: str) -> Principal | None:
        match = _KEY.match(presented or "")
        if match is None:
            return None
        now = self._clock()
        async with self._sessionmaker() as session, session.begin():
            row = (await session.execute(
                select(ApiKey, User.status).join(User, User.id == ApiKey.user_id)
                .where(ApiKey.prefix == match.group(1))
            )).one_or_none()
            if row is None:
                digest(presented)  # 前缀不存在时也做同样的计算，减少时间差
                return None
            key, status = row
            if not same(digest(presented), key.secret_hash):
                return None
            if key.revoked_at is not None or status != USER_ACTIVE:
                return None
            if key.expires_at is not None and key.expires_at <= now:
                return None
            if key.last_used_at is None or now - key.last_used_at >= self._touch_interval:
                key.last_used_at = now
            return Principal(user_id=key.user_id, auth_method="api_key", api_key_id=key.id,
                             scopes=frozenset(key.scopes), workspace_id=key.workspace_id)
