"""跳转登录的路由。失败一律返回同一句提示，具体原因只记日志类别、不对外，也不记授权码、state、令牌。

- 环境变量配的 OIDC：/api/auth/oidc/login 与 /callback（客户在身份中台登记过这个回调地址，保留不动）；
- 后台「登录方式」里配的与扩展提供的（企业微信等）：/api/auth/sso/{id}/login 与 /callback，回调地址在后台页面上给管理员抄。
state 的 Cookie 只发给对应的回调路径，不同登录方式互不串用。

在企业微信等客户端里打开（浏览器标识命中登录方式的 in_client_agents，或带 in_client=1，例如企业微信应用主页填的地址）
走客户端里的免登授权；回调是同一个。
"""

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, HTTPException, Path, Request
from fastapi.responses import RedirectResponse

from ..audit.context import annotate
from .errors import SsoFailure
from .paths import OIDC_CALLBACK_PATH, sso_callback_path
from .sso import STATE_TTL, SsoProvider

if TYPE_CHECKING:
    from .container import Identity

log = logging.getLogger(__name__)
SSO_FAILED = "单点登录失败"
ProviderId = Annotated[str, Path(pattern=r"^[A-Za-z0-9_.\-]{1,64}$")]


def opened_in_client(request: Request, provider: SsoProvider) -> bool:
    agent = request.headers.get("user-agent", "").lower()
    return any(marker.lower() in agent for marker in provider.in_client_agents)


def add_sso_routes(router: APIRouter, identity: "Identity", set_session_cookie: Callable) -> None:
    config = identity.config

    async def start(provider: SsoProvider, cookie_name: str, cookie_path: str,
                    in_client: bool = False) -> RedirectResponse:
        try:
            started = await identity.sso.start(provider, in_client=in_client)
        except SsoFailure as error:
            log.warning("sso login start failed for %s: %s", provider.id, error)
            raise HTTPException(status_code=502, detail=SSO_FAILED) from error
        response = RedirectResponse(started.redirect_url, status_code=302)
        response.set_cookie(cookie_name, started.cookie_value, max_age=int(STATE_TTL.total_seconds()),
                            path=cookie_path, httponly=True, secure=config.cookie_secure, samesite="lax")
        return response

    async def finish(request: Request, provider: SsoProvider, cookie_name: str, cookie_path: str,
                     code: str, state: str) -> RedirectResponse:
        annotate(record=True)  # 登录走 GET，也要进审计
        try:
            if request.query_params.get("error"):
                raise SsoFailure("提供方返回错误")
            issued = await identity.sso.complete(provider, code=code, state=state,
                                                 cookie_value=request.cookies.get(cookie_name, ""))
        except SsoFailure as error:
            log.info("sso callback rejected for %s: %s", provider.id, error)
            raise HTTPException(status_code=401, detail=SSO_FAILED) from error
        response = RedirectResponse(provider.post_login_redirect, status_code=302)
        response.delete_cookie(cookie_name, path=cookie_path, httponly=True, secure=config.cookie_secure,
                               samesite="lax")
        set_session_cookie(response, identity, issued)
        annotate(actor_id=issued.user_id or None, resource_id=issued.user_id or None)
        return response

    def env_provider() -> SsoProvider:
        provider = identity.providers.env()
        if provider is None:
            raise HTTPException(status_code=404, detail="未启用单点登录")
        return provider

    async def configured(provider_id: str) -> SsoProvider:
        try:
            provider = await identity.redirect_logins.resolve(provider_id)
        except SsoFailure as error:
            log.warning("sso provider %s unusable: %s", provider_id, error)
            raise HTTPException(status_code=502, detail=SSO_FAILED) from error
        if provider is None:
            raise HTTPException(status_code=404, detail="没有这个登录方式")
        return provider

    @router.get("/oidc/login")
    async def oidc_login():
        """跳转到单点登录（环境变量配置的 OIDC）"""
        return await start(env_provider(), config.oidc_state_cookie_name, OIDC_CALLBACK_PATH)

    @router.get("/oidc/callback")
    async def oidc_callback(request: Request, code: str = "", state: str = ""):
        """单点登录回调（环境变量配置的 OIDC）"""
        return await finish(request, env_provider(), config.oidc_state_cookie_name, OIDC_CALLBACK_PATH, code, state)

    @router.get("/sso/{provider_id}/login")
    async def sso_login(provider_id: ProviderId, request: Request, in_client: bool = False):
        """跳转到外部登录

        后台「登录方式」里配置的 OIDC、OAuth2，扩展提供的企业微信等，登录页的按钮指向这里。
        在企业微信等客户端里打开（或带 in_client=1）时走客户端里的免登。
        """
        provider = await configured(provider_id)
        return await start(provider, config.sso_state_cookie_name, sso_callback_path(provider.id),
                           in_client=in_client or opened_in_client(request, provider))

    @router.get("/sso/{provider_id}/callback")
    async def sso_callback(provider_id: ProviderId, request: Request, code: str = "", state: str = ""):
        """外部登录回调"""
        provider = await configured(provider_id)
        return await finish(request, provider, config.sso_state_cookie_name, sso_callback_path(provider.id),
                            code, state)
