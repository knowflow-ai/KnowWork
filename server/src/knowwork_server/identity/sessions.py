"""服务端浏览器会话：可吊销、可过期、可选滑动续期；用户被禁用后立即失效。"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..auth.principal import Principal
from .credentials import Clock, digest, new_id, new_token, utcnow
from .models import USER_ACTIVE, AuthSession, User

SESSION_METHODS = frozenset({"session", "oidc"})
MAX_TOKEN_LENGTH = 128


@dataclass(frozen=True)
class IssuedSession:
    session_id: str
    token: str
    expires_at: datetime
    user_id: str = ""


class SessionService:
    def __init__(self, sessionmaker: async_sessionmaker, *, ttl: timedelta, sliding: bool,
                 clock: Clock = utcnow) -> None:
        self._sessionmaker = sessionmaker
        self._ttl = ttl
        self._sliding = sliding
        self._clock = clock

    async def create(self, user_id: str, *, method: str = "session") -> IssuedSession:
        async with self._sessionmaker() as session, session.begin():
            return await self.create_in(session, user_id, method=method)

    async def create_in(self, session: AsyncSession, user_id: str, *, method: str = "session") -> IssuedSession:
        """在调用方的事务里签发会话（邀请注册后直接登录、OIDC 回调共用）。"""
        if method not in SESSION_METHODS:
            raise ValueError("未知的会话类型")
        now = self._clock()
        token = new_token()
        record = AuthSession(id=new_id(), token_hash=digest(token), user_id=user_id, auth_method=method,
                             created_at=now, expires_at=now + self._ttl, last_seen_at=now)
        session.add(record)
        await session.flush()
        return IssuedSession(session_id=record.id, token=token, expires_at=record.expires_at, user_id=user_id)

    async def resolve(self, token: str) -> Principal | None:
        """令牌有效、未吊销、未过期且用户仍为启用状态才返回身份。"""
        if not token or len(token) > MAX_TOKEN_LENGTH:
            return None
        now = self._clock()
        async with self._sessionmaker() as session, session.begin():
            row = (await session.execute(
                select(AuthSession, User.status)
                .join(User, User.id == AuthSession.user_id)
                .where(AuthSession.token_hash == digest(token))
            )).one_or_none()
            if row is None:
                return None
            record, status = row
            if record.revoked_at is not None or record.expires_at <= now or status != USER_ACTIVE:
                return None
            record.last_seen_at = now
            if self._sliding and record.expires_at - now < self._ttl / 2:
                record.expires_at = now + self._ttl
            return Principal(user_id=record.user_id, auth_method=record.auth_method, session_id=record.id)

    async def revoke(self, session_id: str) -> None:
        async with self._sessionmaker() as session, session.begin():
            await session.execute(update(AuthSession)
                                  .where(AuthSession.id == session_id, AuthSession.revoked_at.is_(None))
                                  .values(revoked_at=self._clock()))

    async def revoke_all(self, user_id: str) -> None:
        async with self._sessionmaker() as session, session.begin():
            await session.execute(update(AuthSession)
                                  .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
                                  .values(revoked_at=self._clock()))
