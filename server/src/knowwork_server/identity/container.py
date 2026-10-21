"""身份模块的装配：各服务共用一个 sessionmaker 与时钟，对外暴露 get_principal 与 require_scopes 依赖。"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx
from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..audit.context import note_principal
from ..auth.principal import Principal
from .api_keys import ApiKeyService
from .config import IdentityConfig
from .credentials import Clock, utcnow
from .hooks import UserCreatedHook
from .invitations import InvitationService
from .oidc_client import OidcClient
from .oidc_login import OidcLoginService
from .sessions import SessionService
from .throttle import InMemoryLoginThrottle, Limits, LoginThrottle
from .users import UserService

UNAUTHORIZED = "未登录或凭证无效"
FORBIDDEN_SCOPE = "凭证的权限范围不足"
API_KEY_PREFIX = "ak_"


def unauthorized() -> HTTPException:
    return HTTPException(status_code=401, detail=UNAUTHORIZED, headers={"WWW-Authenticate": "Bearer"})


@dataclass(frozen=True)
class Identity:
    config: IdentityConfig
    users: UserService
    sessions: SessionService
    invitations: InvitationService
    api_keys: ApiKeyService
    throttle: LoginThrottle
    oidc: OidcLoginService | None

    async def get_principal(self, request: Request) -> Principal:
        """先认 Cookie 会话，再认 `Authorization: Bearer ak_...`；都不成立统一 401，不区分原因。"""
        token = request.cookies.get(self.config.session_cookie_name)
        if token:
            principal = await self.sessions.resolve(token)
            if principal is not None:
                note_principal(principal)  # 审计据此记操作者与凭证 id，不再自己解析令牌
                return principal
        scheme, _, credential = (request.headers.get("authorization") or "").partition(" ")
        if scheme.lower() == "bearer" and credential.startswith(API_KEY_PREFIX):
            principal = await self.api_keys.resolve(credential.strip())
            if principal is not None:
                note_principal(principal)
                return principal
        raise unauthorized()

    def require_scopes(self, *scopes: str) -> Callable[[Request], Awaitable[Principal]]:
        """依赖工厂：API Key 必须带齐所需范围，否则 403；会话身份不受范围限制。"""
        required = tuple(scopes)

        async def dependency(request: Request) -> Principal:
            principal = await self.get_principal(request)
            if not principal.allows(*required):
                raise HTTPException(status_code=403, detail=FORBIDDEN_SCOPE)
            return principal

        return dependency


def build_identity(sessionmaker: async_sessionmaker, config: IdentityConfig, *,
                   oidc_http: httpx.AsyncClient | None = None, clock: Clock = utcnow,
                   throttle: LoginThrottle | None = None,
                   on_user_created: UserCreatedHook | None = None) -> Identity:
    """oidc_http 由调用方持有并负责关闭；配置了 OIDC 却没给时内部新建一个。"""
    sessions = SessionService(sessionmaker, ttl=config.session_ttl, sliding=config.session_sliding, clock=clock)
    link_base = config.public_base_url.rstrip("/") + config.invite_path
    invitations = InvitationService(sessionmaker, sessions, ttl=config.invitation_ttl, link_base=link_base,
                                    clock=clock, on_user_created=on_user_created)
    oidc = None
    if config.oidc is not None:
        client = OidcClient(config.oidc, oidc_http or httpx.AsyncClient())
        oidc = OidcLoginService(sessionmaker, client, sessions, invitations, clock=clock,
                                on_user_created=on_user_created)
    limits = Limits(window_seconds=config.login_failure_window.total_seconds(),
                    per_account=config.login_max_failures_per_account, per_ip=config.login_max_failures_per_ip)
    return Identity(
        config=config,
        users=UserService(sessionmaker, clock=clock),
        sessions=sessions,
        invitations=invitations,
        api_keys=ApiKeyService(sessionmaker, touch_interval=config.api_key_touch_interval, clock=clock),
        throttle=throttle or InMemoryLoginThrottle(limits),
        oidc=oidc,
    )
