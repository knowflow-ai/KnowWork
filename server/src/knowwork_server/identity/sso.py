"""跳转类登录的公共流程：开始（生成 state、nonce、PKCE 校验串）与回调（核对后交给 external.py 认人、签发会话）。

OIDC、通用 OAuth2、扩展提供的企业微信（以后钉钉、飞书等）只实现 RedirectClient 的两步：拼授权地址、拿授权码换外部身份。
后台配的与扩展提供的都登记进 RedirectLogins，路由按登录方式 id 找。
state、nonce、code_verifier 的明文只在浏览器（短期 Cookie 与 URL）里，库里只有摘要；回调时要求 URL 里的 state
与 Cookie 里的一致（防登录 CSRF），库里的记录一次性消费，并且必须是同一个登录方式发的。
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker

from .credentials import Clock, digest, new_token, same, utcnow
from .errors import SsoFailure
from .external import ExternalProfile, ExternalSignIn
from .models import LoginState
from .resolvers import Resolvers
from .sessions import IssuedSession

MAX_PARAM_LENGTH = 2048
STATE_TTL = timedelta(minutes=10)


class RedirectClient(Protocol):
    async def authorization_url(self, *, state: str, nonce: str, code_verifier: str, in_client: bool = False) -> str:
        """in_client：在企业微信等客户端的内置浏览器里打开，换成客户端里免登的授权方式；不区分的忽略它。"""
        ...

    async def profile(self, *, code: str, code_verifier: str, nonce_hash: str) -> ExternalProfile:
        """用授权码换对方的身份；失败抛 SsoFailure。nonce_hash 给 OIDC 核对 id_token 里的 nonce。"""
        ...


@dataclass(frozen=True)
class SsoProvider:
    id: str              # 路由与登录状态里的 id；环境变量配的 OIDC 是 "oidc"
    kind: str            # 会话记的登录方式：oidc、oauth2……
    client: RedirectClient
    allow_signup: bool   # 首次登录自动建号；否则只允许已有账号与被邀请的
    require_email: bool = False
    post_login_redirect: str = "/"
    # 在哪些客户端里打开算「在客户端里」（按浏览器标识里的片段认，企业微信是 wxwork），同登录方式列表里的
    in_client_agents: tuple[str, ...] = ()


@dataclass(frozen=True)
class SsoStart:
    redirect_url: str
    cookie_value: str


def _split_cookie(cookie_value: str) -> tuple[str, str]:
    state, sep, verifier = (cookie_value or "").partition(".")
    if not sep or not state or not verifier or len(cookie_value) > MAX_PARAM_LENGTH:
        raise SsoFailure("登录状态缺失")
    return state, verifier


class SsoLogin:
    def __init__(self, sessionmaker: async_sessionmaker, external: ExternalSignIn, clock: Clock = utcnow) -> None:
        self._sessionmaker = sessionmaker
        self._external = external
        self._clock = clock

    async def start(self, provider: SsoProvider, *, in_client: bool = False) -> SsoStart:
        state, nonce, verifier = new_token(), new_token(), new_token(48)
        url = await provider.client.authorization_url(state=state, nonce=nonce, code_verifier=verifier,
                                                      in_client=in_client)
        now = self._clock()
        async with self._sessionmaker() as session, session.begin():
            await session.execute(delete(LoginState).where(LoginState.expires_at <= now))
            session.add(LoginState(state_hash=digest(state), method=provider.id, nonce_hash=digest(nonce),
                                   verifier_hash=digest(verifier), expires_at=now + STATE_TTL))
        return SsoStart(redirect_url=url, cookie_value=f"{state}.{verifier}")

    async def complete(self, provider: SsoProvider, *, code: str, state: str, cookie_value: str) -> IssuedSession:
        cookie_state, verifier = _split_cookie(cookie_value)
        if not code or len(code) > MAX_PARAM_LENGTH:
            raise SsoFailure("授权码缺失")
        if not same(state or "", cookie_state):
            raise SsoFailure("state 不匹配")
        login_state = await self._consume_state(cookie_state, provider.id)
        if not same(digest(verifier), login_state.verifier_hash):
            raise SsoFailure("PKCE 校验串不匹配")
        profile = await provider.client.profile(code=code, code_verifier=verifier, nonce_hash=login_state.nonce_hash)
        return await self._external.sign_in(profile, method=provider.kind, allow_signup=provider.allow_signup,
                                            require_email=provider.require_email)

    async def _consume_state(self, state: str, method: str) -> LoginState:
        async with self._sessionmaker() as session, session.begin():
            record = (await session.execute(
                delete(LoginState).where(LoginState.state_hash == digest(state)).returning(LoginState)
            )).scalar_one_or_none()
        if record is None or record.method != method or record.expires_at <= self._clock():
            raise SsoFailure("登录状态已失效")
        return record


class RedirectLogins(Resolvers[SsoProvider]):
    """跳转类登录方式：后台「登录方式」里配的（providers.py）在前，扩展登记的（企业微信等）在后。"""
