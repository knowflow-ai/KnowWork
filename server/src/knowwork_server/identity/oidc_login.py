"""OIDC 登录流程：开始（生成 state/nonce/PKCE）与回调（校验后按外部身份或 email 匹配用户并签发会话）。

state、nonce、code_verifier 的明文只在浏览器（短期 Cookie 与 URL）里，库里只有摘要；
回调时要求 URL 里的 state 与 Cookie 里的一致（防登录 CSRF），并且库里的记录一次性消费。
"""

from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .credentials import Clock, digest, new_token, same, utcnow
from .errors import EmailTaken, InvalidInput, OidcFailure
from .hooks import Placement, UserCreatedHook, notify_user_created
from .invitations import InvitationService
from .models import USER_ACTIVE, OidcIdentity, OidcLoginState, User
from .oidc_client import OidcClient
from .sessions import IssuedSession, SessionService
from .users import insert_user, normalize_email

MAX_PARAM_LENGTH = 2048


@dataclass(frozen=True)
class OidcStart:
    redirect_url: str
    cookie_value: str


def _split_cookie(cookie_value: str) -> tuple[str, str]:
    state, sep, verifier = (cookie_value or "").partition(".")
    if not sep or not state or not verifier or len(cookie_value) > MAX_PARAM_LENGTH:
        raise OidcFailure("登录状态缺失")
    return state, verifier


class OidcLoginService:
    def __init__(self, sessionmaker: async_sessionmaker, client: OidcClient, sessions: SessionService,
                 invitations: InvitationService, clock: Clock = utcnow,
                 on_user_created: UserCreatedHook | None = None) -> None:
        self._sessionmaker = sessionmaker
        self._client = client
        self._sessions = sessions
        self._invitations = invitations
        self._clock = clock
        self._on_user_created = on_user_created

    @property
    def config(self):
        return self._client.config

    async def start(self) -> OidcStart:
        state, nonce, verifier = new_token(), new_token(), new_token(48)
        url = await self._client.authorization_url(state=state, nonce=nonce, code_verifier=verifier)
        now = self._clock()
        async with self._sessionmaker() as session, session.begin():
            await session.execute(delete(OidcLoginState).where(OidcLoginState.expires_at <= now))
            session.add(OidcLoginState(state_hash=digest(state), nonce_hash=digest(nonce),
                                       verifier_hash=digest(verifier),
                                       expires_at=now + self._client.config.state_ttl))
        return OidcStart(redirect_url=url, cookie_value=f"{state}.{verifier}")

    async def complete(self, *, code: str, state: str, cookie_value: str) -> IssuedSession:
        cookie_state, verifier = _split_cookie(cookie_value)
        if not code or len(code) > MAX_PARAM_LENGTH:
            raise OidcFailure("授权码缺失")
        if not same(state or "", cookie_state):
            raise OidcFailure("state 不匹配")
        login_state = await self._consume_state(cookie_state)
        if not same(digest(verifier), login_state.verifier_hash):
            raise OidcFailure("PKCE 校验串不匹配")
        id_token = await self._client.exchange_code(code=code, code_verifier=verifier)
        claims = await self._client.validate_id_token(id_token)
        if not same(digest(str(claims["nonce"])), login_state.nonce_hash):
            raise OidcFailure("nonce 不匹配")
        created: list[tuple[str, Placement | None]] = []
        async with self._sessionmaker() as session, session.begin():
            user_id = await self._match_user(session, claims, created)
            issued = await self._sessions.create_in(session, user_id, method="oidc")
        for new_user_id, placement in created:
            await notify_user_created(self._on_user_created, new_user_id, placement)
        return issued

    async def _consume_state(self, state: str) -> OidcLoginState:
        async with self._sessionmaker() as session, session.begin():
            record = (await session.execute(
                delete(OidcLoginState).where(OidcLoginState.state_hash == digest(state))
                .returning(OidcLoginState)
            )).scalar_one_or_none()
        if record is None or record.expires_at <= self._clock():
            raise OidcFailure("登录状态已失效")
        return record

    async def _match_user(self, session: AsyncSession, claims: dict,
                          created: list[tuple[str, Placement | None]]) -> str:
        issuer, subject = self._client.config.issuer, str(claims["sub"])
        linked = await session.get(OidcIdentity, (issuer, subject))
        user = await session.get(User, linked.user_id) if linked else await self._find_or_create(session, claims,
                                                                                                  created)
        if user is None or user.status != USER_ACTIVE:
            raise OidcFailure("用户不可登录")
        if linked is None:
            session.add(OidcIdentity(issuer=issuer, subject=subject, user_id=user.id, created_at=self._clock()))
        return user.id

    async def _find_or_create(self, session: AsyncSession, claims: dict,
                              created: list[tuple[str, Placement | None]]) -> User | None:
        email = claims.get("email")
        verified = claims.get("email_verified") is True or self._client.config.trust_unverified_email
        if not isinstance(email, str) or not verified:
            raise OidcFailure("身份提供方未给出已验证的邮箱")
        try:
            normalized = normalize_email(email)
        except InvalidInput as error:
            raise OidcFailure("邮箱格式不正确") from error
        existing = (await session.execute(select(User).where(User.email == normalized))).scalar_one_or_none()
        if existing is not None:
            return existing
        invited = await self._invitations.consume_for_email(session, normalized)
        if self._client.config.allow_only_known_users and invited is None:
            raise OidcFailure("未被邀请的用户")
        name = claims.get("name") if isinstance(claims.get("name"), str) else ""
        try:
            user = await insert_user(session, email=normalized, name=name, password=None)
        except EmailTaken as error:
            raise OidcFailure("建号冲突") from error
        created.append((user.id, invited))
        return user
