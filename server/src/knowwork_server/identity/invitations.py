"""邀请注册：管理员生成一次性、有过期的邀请链接（库里只存摘要），被邀请人凭链接设密码完成注册。"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .credentials import Clock, digest, new_id, new_token, utcnow
from .errors import EmailTaken, InvalidInvitation, NotAllowed
from .hooks import UserCreatedHook, notify_user_created
from .models import Invitation, User
from .sessions import IssuedSession, SessionService
from .users import UserInfo, check_password_policy, insert_user, normalize_email, to_info

MAX_TOKEN_LENGTH = 128


@dataclass(frozen=True)
class IssuedInvitation:
    id: str
    email: str
    token: str
    url: str
    expires_at: datetime


@dataclass(frozen=True)
class AcceptedInvitation:
    user: UserInfo
    session: IssuedSession


def _usable(invitation: Invitation, now: datetime) -> bool:
    return invitation.accepted_at is None and invitation.revoked_at is None and invitation.expires_at > now


class InvitationService:
    def __init__(self, sessionmaker: async_sessionmaker, sessions: SessionService, *, ttl: timedelta,
                 link_base: str, clock: Clock = utcnow, on_user_created: UserCreatedHook | None = None) -> None:
        self._sessionmaker = sessionmaker
        self._sessions = sessions
        self._ttl = ttl
        self._link_base = link_base
        self._clock = clock
        self._on_user_created = on_user_created

    async def create(self, *, email: str, invited_by: str, caller_is_admin: bool) -> IssuedInvitation:
        if not caller_is_admin:
            raise NotAllowed("只有管理员可以邀请")
        normalized = normalize_email(email)
        now = self._clock()
        token = new_token()
        async with self._sessionmaker() as session, session.begin():
            exists = (await session.execute(select(User.id).where(User.email == normalized))).first()
            if exists:
                raise EmailTaken("该邮箱已注册")
            record = Invitation(id=new_id(), token_hash=digest(token), email=normalized, invited_by=invited_by,
                                created_at=now, expires_at=now + self._ttl)
            session.add(record)
        return IssuedInvitation(id=record.id, email=normalized, token=token,
                                url=f"{self._link_base}?token={token}", expires_at=record.expires_at)

    async def revoke(self, invitation_id: str, *, caller_is_admin: bool) -> bool:
        if not caller_is_admin:
            raise NotAllowed("只有管理员可以撤销邀请")
        async with self._sessionmaker() as session, session.begin():
            result = await session.execute(
                update(Invitation)
                .where(Invitation.id == invitation_id, Invitation.accepted_at.is_(None),
                       Invitation.revoked_at.is_(None))
                .values(revoked_at=self._clock()))
            return result.rowcount > 0

    async def accept(self, *, token: str, name: str, password: str) -> AcceptedInvitation:
        """凭邀请设密码注册并直接登录。邀请无效、过期、已用、已撤销统一报同一个错。"""
        check_password_policy(password)
        if not token or len(token) > MAX_TOKEN_LENGTH:
            raise InvalidInvitation("邀请无效或已过期")
        now = self._clock()
        async with self._sessionmaker() as session, session.begin():
            invitation = (await session.execute(
                select(Invitation).where(Invitation.token_hash == digest(token)).with_for_update()
            )).scalar_one_or_none()
            if invitation is None or not _usable(invitation, now):
                raise InvalidInvitation("邀请无效或已过期")
            try:
                user = await insert_user(session, email=invitation.email, name=name, password=password)
            except EmailTaken as error:
                raise InvalidInvitation("邀请无效或已过期") from error
            invitation.accepted_at = now
            issued = await self._sessions.create_in(session, user.id)
            accepted = AcceptedInvitation(user=to_info(user), session=issued)
        await notify_user_created(self._on_user_created, accepted.user.id)
        return accepted

    async def consume_for_email(self, session: AsyncSession, email: str) -> bool:
        """OIDC 首次登录：该邮箱有可用邀请则标记为已接受并返回 True（在调用方事务里）。"""
        now = self._clock()
        invitation = (await session.execute(
            select(Invitation)
            .where(Invitation.email == email, Invitation.accepted_at.is_(None), Invitation.revoked_at.is_(None),
                   Invitation.expires_at > now)
            .order_by(Invitation.created_at.desc()).limit(1).with_for_update()
        )).scalar_one_or_none()
        if invitation is None:
            return False
        invitation.accepted_at = now
        return True
