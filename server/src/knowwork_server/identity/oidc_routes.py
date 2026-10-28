"""OIDC 单点登录的两个路由。失败一律返回同一句提示，具体原因不对外。"""

import logging
from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from ..audit.context import annotate
from .container import Identity
from .errors import OidcFailure

log = logging.getLogger(__name__)
SSO_FAILED = "单点登录失败"
CALLBACK_PATH = "/api/auth/oidc/callback"


def add_oidc_routes(router: APIRouter, identity: Identity, set_session_cookie: Callable) -> None:
    def service():
        if identity.oidc is None:
            raise HTTPException(status_code=404, detail="未启用单点登录")
        return identity.oidc

    cookie_name = identity.config.oidc_state_cookie_name

    @router.get("/oidc/login")
    async def oidc_login():
        """跳转到单点登录"""
        try:
            started = await service().start()
        except OidcFailure as error:
            log.warning("oidc login start failed: %s", type(error).__name__)
            raise HTTPException(status_code=502, detail=SSO_FAILED) from error
        response = RedirectResponse(started.redirect_url, status_code=302)
        ttl = int(identity.oidc.config.state_ttl.total_seconds())
        response.set_cookie(cookie_name, started.cookie_value, max_age=ttl, path=CALLBACK_PATH, httponly=True,
                            secure=identity.config.cookie_secure, samesite="lax")
        return response

    @router.get("/oidc/callback")
    async def oidc_callback(request: Request, code: str = "", state: str = ""):
        """单点登录回调"""
        annotate(record=True)  # 登录走 GET，也要进审计
        login = service()
        try:
            if request.query_params.get("error"):
                raise OidcFailure("提供方返回错误")
            issued = await login.complete(code=code, state=state, cookie_value=request.cookies.get(cookie_name, ""))
        except OidcFailure as error:
            # 只记录失败类别，不记录 code、state、令牌等任何凭证。
            log.info("oidc callback rejected: %s", error)
            raise HTTPException(status_code=401, detail=SSO_FAILED) from error
        response = RedirectResponse(login.config.post_login_redirect, status_code=302)
        response.delete_cookie(cookie_name, path=CALLBACK_PATH, httponly=True,
                               secure=identity.config.cookie_secure, samesite="lax")
        set_session_cookie(response, identity, issued)
        annotate(actor_id=issued.user_id or None, resource_id=issued.user_id or None)
        return response
