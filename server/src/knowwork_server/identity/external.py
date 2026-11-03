"""外部登录的最后一段：把对方给的身份（ExternalProfile）对到本地用户并签发会话。

OIDC、OAuth2、域账号、企业微信等登录方式只负责把回调或账号密码换成 ExternalProfile，认人的规则都在这里：
1. 身份域 + 外部 ID 已经绑过 → 就是这个人（对方改了邮箱、不再给邮箱也认得出）；
2. 对方给了可信的邮箱（OIDC 的 email_verified、管理员确认由企业统一分配的）且本地有这个邮箱 → 绑上；
3. 这个邮箱有可用的邀请 → 按邀请建号，去邀请定的空间；
4. 都没有：登录方式允许首次登录自动建号就建（可以没有邮箱），否则拒绝。
不可信的邮箱从不参与认人，也不写进新账号：否则谁都能在自己的身份中台里填别人的邮箱登进别人的账号。
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .credentials import Clock, utcnow
from .errors import EmailTaken, InvalidInput, SsoFailure
from .hooks import Placement, UserCreatedHook, notify_user_created
from .invitations import InvitationService
from .models import USER_ACTIVE, USER_PERSON, ExternalIdentity, User
from .sessions import IssuedSession, SessionService
from .users import insert_user, normalize_email

MAX_SUBJECT_LENGTH = 255
MAX_REALM_LENGTH = 512


@dataclass(frozen=True)
class ExternalProfile:
    realm: str
    subject: str
    name: str = ""
    email: str | None = None
    email_trusted: bool = False
    account: str | None = None
    aliases: Mapping[str, str] = field(default_factory=dict)


class ExternalSignIn:
    def __init__(self, sessionmaker: async_sessionmaker, sessions: SessionService, invitations: InvitationService,
                 clock: Clock = utcnow, on_user_created: UserCreatedHook | None = None) -> None:
        self._sessionmaker = sessionmaker
        self._sessions = sessions
        self._invitations = invitations
        self._clock = clock
        self._on_user_created = on_user_created

    async def sign_in(self, profile: ExternalProfile, *, method: str, allow_signup: bool,
                      require_email: bool = False) -> IssuedSession:
        """require_email：没绑过的人必须给出可信邮箱（OIDC 照旧这么要求）。失败一律抛 SsoFailure。"""
        _check(profile)
        created: list[tuple[str, Placement | None]] = []
        async with self._sessionmaker() as session, session.begin():
            user_id = await self._match(session, profile, allow_signup, require_email, created)
            issued = await self._sessions.create_in(session, user_id, method=method)
        for new_user_id, placement in created:
            await notify_user_created(self._on_user_created, new_user_id, placement)
        return issued

    async def _match(self, session: AsyncSession, profile: ExternalProfile, allow_signup: bool, require_email: bool,
                     created: list[tuple[str, Placement | None]]) -> str:
        now = self._clock()
        linked = await session.get(ExternalIdentity, (profile.realm, profile.subject), with_for_update=True)
        if linked is not None:
            user = await session.get(User, linked.user_id)
        else:
            user = await self._find_or_create(session, profile, allow_signup, require_email, created)
        if user is None or user.status != USER_ACTIVE or user.kind != USER_PERSON:
            raise SsoFailure("用户不可登录")
        if linked is None:
            session.add(ExternalIdentity(realm=profile.realm, subject=profile.subject, user_id=user.id,
                                         account=profile.account, aliases=dict(profile.aliases),
                                         created_at=now, last_login_at=now))
        else:
            linked.last_login_at = now
        return user.id

    async def _find_or_create(self, session: AsyncSession, profile: ExternalProfile, allow_signup: bool,
                              require_email: bool, created: list[tuple[str, Placement | None]]) -> User:
        email = _trusted_email(profile)
        if require_email and email is None:
            raise SsoFailure("身份提供方未给出已验证的邮箱")
        if email is not None:
            existing = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
            if existing is not None:
                return existing
        invited = await self._invitations.consume_for_email(session, email) if email else None
        if invited is None and not allow_signup:
            raise SsoFailure("未被邀请的用户")
        try:
            user = await insert_user(session, email=email, name=profile.name or profile.account or "", password=None)
        except EmailTaken as error:
            raise SsoFailure("建号冲突") from error
        created.append((user.id, invited))
        return user


def _check(profile: ExternalProfile) -> None:
    if not profile.realm or len(profile.realm) > MAX_REALM_LENGTH:
        raise SsoFailure("身份域不合法")
    if not profile.subject or len(profile.subject) > MAX_SUBJECT_LENGTH:
        raise SsoFailure("外部 ID 不合法")


def _trusted_email(profile: ExternalProfile) -> str | None:
    if not profile.email_trusted or not profile.email:
        return None
    try:
        return normalize_email(profile.email)
    except InvalidInput as error:
        raise SsoFailure("邮箱格式不正确") from error
