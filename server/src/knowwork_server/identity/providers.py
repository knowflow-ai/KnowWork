"""登录方式的配置与运行：环境变量配的 OIDC（只读）加上后台「登录方式」里配的 OIDC、通用 OAuth2。

- 登录时按 id 取出 SsoProvider（跳转登录的公共流程用它）；客户端按 (id, 更新时间) 缓存，OIDC 的发现文档与
  JWKS 不用每次登录都重新取，改了配置自动换新；
- 后台的增删改只给系统管理员（路由层管），客户端密钥加密存、只给末四位；
- 登录页的入口列表（GET /api/auth/methods）由 login_methods 给出：环境变量的 OIDC 在前，后台配的按建立先后。
"""

import secrets as token_bytes
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..knowledge.crypto import SecretError, mask
from .config import OidcConfig
from .credentials import Clock, utcnow
from .errors import Conflict, InvalidInput, NotFound, SsoFailure
from .methods import LoginMethod
from .models import LoginProvider
from .oauth2_client import OAuth2Client, OAuth2Config
from .oidc_client import OidcClient, OidcRedirect
from .paths import OIDC_LOGIN_PATH, sso_callback_path, sso_login_path
from .provider_schemas import ProviderCreate, ProviderUpdate, secret_target, settings_for
from .sso import RedirectClient, SsoProvider

ENV_OIDC_ID = "oidc"
ENV_OIDC_NAME = "企业账号"
SECRET_PURPOSE = "login-provider-secrets"
MAX_PROVIDERS = 20


class Sealer(Protocol):
    """密钥加解密（knowledge.crypto.SecretBox 的形状），由装配方按部署主密钥派生后给进来。"""

    def seal(self, plaintext: str, *, context: str) -> str: ...

    def open(self, token: str, *, context: str) -> str: ...


@dataclass(frozen=True)
class ProviderView:
    id: str
    kind: str
    name: str
    enabled: bool
    signup: str
    config: dict
    has_secret: bool
    secret_last4: str
    callback_url: str
    readonly: bool
    created_at: datetime | None
    updated_at: datetime | None


def _context(provider_id: str) -> str:
    return f"login_provider:{provider_id}:client_secret"


class LoginProviders:
    def __init__(self, sessionmaker: async_sessionmaker, *, public_base_url: str, env_oidc: OidcConfig | None,
                 http: httpx.AsyncClient | None, sealer: Sealer | None, clock: Clock = utcnow) -> None:
        self._sessionmaker = sessionmaker
        self._base = public_base_url.rstrip("/")
        self._env = env_oidc
        self._http = http
        self._sealer = sealer
        self._clock = clock
        self._clients: dict[str, tuple[datetime, RedirectClient]] = {}
        self._env_client: RedirectClient | None = None

    def _outbound(self) -> httpx.AsyncClient:
        if self._http is None:  # 调用方没给时用到才建（测试与没配外部登录的部署不建）
            self._http = httpx.AsyncClient(timeout=10.0)
        return self._http

    # ---- 登录时用 ----

    def env(self) -> SsoProvider | None:
        if self._env is None:
            return None
        if self._env_client is None:
            self._env_client = OidcRedirect(OidcClient(self._env, self._outbound()))
        return SsoProvider(id=ENV_OIDC_ID, kind="oidc", client=self._env_client,
                           allow_signup=not self._env.allow_only_known_users, require_email=True,
                           post_login_redirect=self._env.post_login_redirect)

    async def resolve(self, provider_id: str) -> SsoProvider | None:
        """后台配的、开着的登录方式；没有或停用了返回 None。"""
        async with self._sessionmaker() as session:
            row = await session.get(LoginProvider, provider_id)
        if row is None or not row.enabled:
            return None
        return SsoProvider(id=row.id, kind=row.kind, client=self._client(row), allow_signup=row.signup == "auto",
                           require_email=row.kind == "oidc")

    def _client(self, row: LoginProvider) -> RedirectClient:
        cached = self._clients.get(row.id)
        if cached is not None and cached[0] == row.updated_at:
            return cached[1]
        secret = self._open(row)
        redirect_uri = self._base + sso_callback_path(row.id)
        if row.kind == "oidc":
            config = row.config
            client: RedirectClient = OidcRedirect(OidcClient(OidcConfig(
                issuer=config["issuer"], client_id=config["client_id"], client_secret=secret,
                redirect_uri=redirect_uri, scopes=tuple(config["scopes"]), algorithms=tuple(config["algorithms"]),
                trust_unverified_email=config["trust_unverified_email"]), self._outbound()))
        else:
            fields = {key: value for key, value in row.config.items() if key != "scopes"}
            client = OAuth2Client(OAuth2Config(realm=f"oauth2:{row.id}", client_secret=secret,
                                               redirect_uri=redirect_uri, scopes=tuple(row.config["scopes"]),
                                               **fields), self._outbound())
        self._clients[row.id] = (row.updated_at, client)
        return client

    def _open(self, row: LoginProvider) -> str:
        """解不开（换了部署主密钥等）当作这次登录失败，和其他单点登录失败一样处理。"""
        if self._sealer is None or not row.secret_ciphertext:
            raise SsoFailure("登录方式的密钥不可用")
        try:
            return self._sealer.open(row.secret_ciphertext, context=_context(row.id))
        except SecretError as error:
            raise SsoFailure("登录方式的密钥解不开") from error

    async def login_methods(self) -> list[LoginMethod]:
        found = [LoginMethod(id=ENV_OIDC_ID, kind="oidc", label=ENV_OIDC_NAME, style="redirect",
                             url=OIDC_LOGIN_PATH)] if self._env is not None else []
        for row in await self._rows():
            if row.enabled:
                found.append(LoginMethod(id=row.id, kind=row.kind, label=row.name, style="redirect",
                                         url=sso_login_path(row.id)))
        return found

    # ---- 后台管理 ----

    async def views(self) -> list[ProviderView]:
        listed = [self._env_view()] if self._env is not None else []
        return listed + [self._view(row) for row in await self._rows()]

    async def create(self, body: ProviderCreate) -> ProviderView:
        sealer = self._require_sealer()
        config = settings_for(body.kind, body.config)
        now = self._clock()
        provider_id = f"lp_{token_bytes.token_hex(8)}"
        row = LoginProvider(id=provider_id, kind=body.kind, name=body.name.strip(), enabled=body.enabled,
                            signup=body.signup, config=config, created_at=now, updated_at=now,
                            secret_ciphertext=sealer.seal(body.client_secret, context=_context(provider_id)),
                            secret_last4=mask(body.client_secret))
        async with self._sessionmaker() as session, session.begin():
            count = len((await session.execute(select(LoginProvider.id))).all())
            if count >= MAX_PROVIDERS:
                raise InvalidInput(f"登录方式最多 {MAX_PROVIDERS} 个")
            session.add(row)
        return self._view(row)

    async def update(self, provider_id: str, body: ProviderUpdate) -> ProviderView:
        self._writable(provider_id)
        async with self._sessionmaker() as session, session.begin():
            row = await session.get(LoginProvider, provider_id, with_for_update=True)
            if row is None:
                raise NotFound("登录方式不存在")
            if body.name is not None:
                row.name = body.name.strip()
            if body.enabled is not None:
                row.enabled = body.enabled
            if body.signup is not None:
                row.signup = body.signup
            if body.config is not None:
                config = settings_for(row.kind, body.config)
                if body.client_secret is None and secret_target(row.kind, config) != secret_target(row.kind,
                                                                                                    row.config):
                    raise InvalidInput("改了发行方或令牌地址，请重新填写客户端密钥")
                row.config = config
            if body.client_secret is not None:
                row.secret_ciphertext = self._require_sealer().seal(body.client_secret, context=_context(row.id))
                row.secret_last4 = mask(body.client_secret)
            row.updated_at = self._clock()
            return self._view(row)

    async def delete(self, provider_id: str) -> None:
        """删掉以后这种方式登录过的人账号照旧在，以后换别的方式按可信邮箱或管理员绑定再对上。"""
        self._writable(provider_id)
        async with self._sessionmaker() as session, session.begin():
            row = await session.get(LoginProvider, provider_id)
            if row is None:
                raise NotFound("登录方式不存在")
            await session.delete(row)
        self._clients.pop(provider_id, None)

    def _writable(self, provider_id: str) -> None:
        if provider_id == ENV_OIDC_ID and self._env is not None:
            raise Conflict("这是环境变量配置的登录方式，要改请改环境变量")

    def _require_sealer(self) -> Sealer:
        if self._sealer is None:
            raise InvalidInput("没有配置密钥加密，不能保存登录方式")
        return self._sealer

    async def _rows(self) -> list[LoginProvider]:
        async with self._sessionmaker() as session:
            return list((await session.execute(
                select(LoginProvider).order_by(LoginProvider.created_at, LoginProvider.id))).scalars())

    def _view(self, row: LoginProvider) -> ProviderView:
        return ProviderView(id=row.id, kind=row.kind, name=row.name, enabled=row.enabled, signup=row.signup,
                            config=dict(row.config), has_secret=bool(row.secret_ciphertext),
                            secret_last4=row.secret_last4, callback_url=self._base + sso_callback_path(row.id),
                            readonly=False, created_at=row.created_at, updated_at=row.updated_at)

    def _env_view(self) -> ProviderView:
        env = self._env
        assert env is not None
        return ProviderView(id=ENV_OIDC_ID, kind="oidc", name=ENV_OIDC_NAME, enabled=True,
                            signup="known_only" if env.allow_only_known_users else "auto",
                            config={"issuer": env.issuer, "client_id": env.client_id},
                            has_secret=bool(env.client_secret), secret_last4=mask(env.client_secret),
                            callback_url=env.redirect_uri, readonly=True, created_at=None, updated_at=None)
