"""身份模块的装配：各服务共用一个 sessionmaker 与时钟，对外暴露 get_principal 与 require_scopes 依赖。"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import httpx
from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..audit.context import note_principal
from ..auth.principal import Principal
from .api_keys import ApiKeyService
from .config import IdentityConfig
from .credentials import Clock, utcnow
from .directory_accounts import DirectoryAccounts
from .embed import EMBED_PREFIX, EMBED_SCOPES, EmbedCredentials
from .external import ExternalSignIn
from .hooks import UserCreatedHook
from .invitations import InvitationService
from .methods import LoginMethods
from .oauth import ACCESS_PREFIX as OAUTH_ACCESS_PREFIX
from .oauth import OAuthService
from .oauth_clients import OAuthClients
from .oauth_exchange import TokenExchangers
from .password_logins import PasswordLogins
from .providers import LoginProviders, Sealer
from .sessions import SessionService
from .sso import RedirectLogins, SsoLogin
from .throttle import InMemoryLoginThrottle, Limits, LoginThrottle
from .users import UserService
from .verify_files import VerifyFiles

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
    # 登录方式：环境变量配的 OIDC 与后台配的 OIDC、OAuth2（取用、增删改）
    providers: LoginProviders
    # 跳转登录的公共流程（state、PKCE、回调核对）
    sso: SsoLogin
    # 外部登录的最后一段（认人、签发会话），各种外部登录方式共用
    external: ExternalSignIn
    # 目录同步的账号部分（按人员快照建号、对上、停用、恢复），组织同步任务用
    accounts: DirectoryAccounts
    # 登录页显示哪些入口；扩展提供的登录方式也登记到这里
    methods: LoginMethods
    # 账号密码类的外部登录（域账号等），扩展按登录方式 id 登记提供方
    password_logins: PasswordLogins
    # 跳转类的外部登录：后台配的 OIDC、OAuth2，扩展按登录方式 id 登记的（企业微信等）
    redirect_logins: RedirectLogins
    # 域名根目录的校验文件（企业微信验证可信域名），扩展登记按文件名查内容的函数
    verify_files: VerifyFiles
    # 原生应用登录（桌面端等，OAuth 授权码 + PKCE）：客户端由扩展登记到 oauth_clients，没登记时不起作用
    oauth_clients: OAuthClients
    oauth: OAuthService
    # 令牌交换的受众（RFC 8693，identity/oauth_exchange.py）：业务服务、扩展按受众登记怎么签
    oauth_exchangers: TokenExchangers
    # 网页嵌入的会话令牌：渠道模块登记解析函数（identity/embed.py）
    embed: EmbedCredentials = field(default_factory=EmbedCredentials)

    async def get_principal(self, request: Request) -> Principal:
        """先认 Cookie 会话，再认 `Authorization: Bearer` 的 API Key（ak_）或原生应用的访问令牌（kwat_）；
        都不成立统一 401，不区分原因。

        网页嵌入的令牌（emb_）只给声明了范围的接口用（require_scopes），这里一律 401。带着它的请求就算同时带着
        登录 Cookie 也不退回 Cookie：显式给的凭证优先，免得嵌入页里混进浏览器里登录着的那个人。"""
        if _bearer(request).startswith(EMBED_PREFIX):
            raise unauthorized()
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
        if scheme.lower() == "bearer" and credential.startswith(OAUTH_ACCESS_PREFIX):
            principal = await self.oauth.resolve(credential.strip())
            if principal is not None:
                note_principal(principal)
                return principal
        raise unauthorized()

    def require_scopes(self, *scopes: str) -> Callable[[Request], Awaitable[Principal]]:
        """依赖工厂：API Key 必须带齐所需范围，否则 403；会话身份不受范围限制。
        网页嵌入的令牌只在所需范围都在嵌入身份能用的范围里时才认（不声明范围的也不认），否则 403。"""
        required = tuple(scopes)
        embeddable = bool(required) and set(required) <= EMBED_SCOPES

        async def dependency(request: Request) -> Principal:
            credential = _bearer(request)
            if credential.startswith(EMBED_PREFIX):
                if not embeddable:
                    raise HTTPException(status_code=403, detail=FORBIDDEN_SCOPE)
                principal = await self.embed.resolve(credential)
                if principal is None:
                    raise unauthorized()
                note_principal(principal)
            else:
                principal = await self.get_principal(request)
            if not principal.allows(*required):
                raise HTTPException(status_code=403, detail=FORBIDDEN_SCOPE)
            return principal

        return dependency


def _bearer(request: Request) -> str:
    """`Authorization: Bearer <凭证>` 里的凭证；没有或不是 Bearer 时为空串。"""
    scheme, _, credential = (request.headers.get("authorization") or "").partition(" ")
    return credential.strip() if scheme.lower() == "bearer" else ""


def build_identity(sessionmaker: async_sessionmaker, config: IdentityConfig, *,
                   sso_http: httpx.AsyncClient | None = None, secrets: Sealer | None = None, clock: Clock = utcnow,
                   throttle: LoginThrottle | None = None,
                   on_user_created: UserCreatedHook | None = None) -> Identity:
    """sso_http：外部登录出去请求身份中台用，由调用方持有并负责关闭；没给时用到才内部新建。
    secrets：后台登录方式的密钥加解密；没给时不能在后台保存登录方式（环境变量的 OIDC 照常可用）。"""
    sessions = SessionService(sessionmaker, ttl=config.session_ttl, sliding=config.session_sliding, clock=clock)
    link_base = config.public_base_url.rstrip("/") + config.invite_path
    invitations = InvitationService(sessionmaker, sessions, ttl=config.invitation_ttl, link_base=link_base,
                                    clock=clock, on_user_created=on_user_created)
    external = ExternalSignIn(sessionmaker, sessions, invitations, clock=clock, on_user_created=on_user_created)
    providers = LoginProviders(sessionmaker, public_base_url=config.public_base_url, env_oidc=config.oidc,
                               http=sso_http, sealer=secrets, clock=clock)
    methods = LoginMethods()
    methods.add(providers.login_methods)
    redirect_logins = RedirectLogins()
    redirect_logins.add(providers.resolve)
    oauth_clients = OAuthClients()
    oauth = OAuthService(sessionmaker, oauth_clients, code_ttl=config.oauth_code_ttl,
                         access_ttl=config.oauth_access_ttl, refresh_ttl=config.oauth_refresh_ttl,
                         touch_interval=config.api_key_touch_interval, clock=clock)
    limits = Limits(window_seconds=config.login_failure_window.total_seconds(),
                    per_account=config.login_max_failures_per_account, per_ip=config.login_max_failures_per_ip)
    return Identity(
        config=config,
        users=UserService(sessionmaker, clock=clock),
        sessions=sessions,
        invitations=invitations,
        api_keys=ApiKeyService(sessionmaker, touch_interval=config.api_key_touch_interval, clock=clock),
        throttle=throttle or InMemoryLoginThrottle(limits),
        providers=providers,
        sso=SsoLogin(sessionmaker, external, clock=clock),
        external=external,
        accounts=DirectoryAccounts(sessionmaker, clock=clock),
        methods=methods,
        password_logins=PasswordLogins(),
        redirect_logins=redirect_logins,
        verify_files=VerifyFiles(),
        oauth_clients=oauth_clients,
        oauth=oauth,
        oauth_exchangers=TokenExchangers(),
    )
