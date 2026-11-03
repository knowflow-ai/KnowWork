"""最小的假 OAuth2 授权服务器（不带 OIDC）：授权（直接同意）、换令牌（校验客户端凭证、PKCE、回调地址）、用户信息。

很多企业自研的统一认证就是这个形态：没有 id_token，拿访问令牌去调它自己的用户信息接口，字段各家不一样。
用户信息故意套了两层（data.user），测字段路径映射；`token_auth`、`userinfo_token` 切换客户端认证与令牌的传法。
"""

import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlencode

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

BASE = "https://sso.corp.test"
AUTHORIZE_URL = f"{BASE}/oauth/authorize"
TOKEN_URL = f"{BASE}/oauth/token"
USERINFO_URL = f"{BASE}/api/me"
CLIENT_ID = "kw-client"
CLIENT_SECRET = "corp-client-secret"


def _s256(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


class FakeOAuth2:
    def __init__(self) -> None:
        self.user: dict = {"id": 10086, "name": "张三", "mail": "zhangsan@corp.test"}
        self.token_auth = "basic"      # basic：凭证放 Authorization 头；post：放表单里
        self.userinfo_token = "header"  # header：Bearer 头；query：?access_token=
        self.require_pkce = True
        self.token_status = 200
        self.token_as_form = False      # 有的老系统换令牌返回 application/x-www-form-urlencoded
        self.codes: dict[str, dict] = {}
        self.tokens: set[str] = set()
        self.seen_scopes: list[str] = []

    def app(self) -> Starlette:
        return Starlette(routes=[
            Route("/oauth/authorize", self._authorize),
            Route("/oauth/token", self._token, methods=["POST"]),
            Route("/api/me", self._me),
        ])

    async def _authorize(self, request: Request):
        params = dict(request.query_params)
        assert params["client_id"] == CLIENT_ID and params["response_type"] == "code"
        self.seen_scopes.append(params.get("scope", ""))
        code = secrets.token_urlsafe(16)
        self.codes[code] = {"redirect_uri": params["redirect_uri"], "challenge": params.get("code_challenge")}
        return RedirectResponse(f"{params['redirect_uri']}?{urlencode({'code': code, 'state': params['state']})}")

    def _client_ok(self, request: Request, form: dict) -> bool:
        if self.token_auth == "post":
            return form.get("client_id") == CLIENT_ID and form.get("client_secret") == CLIENT_SECRET
        expected = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
        return request.headers.get("authorization") == f"Basic {expected}"

    async def _token(self, request: Request):
        form = {key: values[0] for key, values in parse_qs((await request.body()).decode()).items()}
        grant = self.codes.pop(form.get("code", ""), None)
        if (grant is None or form.get("grant_type") != "authorization_code" or not self._client_ok(request, form)
                or form.get("redirect_uri") != grant["redirect_uri"]):
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        if self.require_pkce and (not grant["challenge"] or _s256(form.get("code_verifier", "")) != grant["challenge"]):
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        if self.token_status != 200:
            return JSONResponse({"error": "server_error"}, status_code=self.token_status)
        token = secrets.token_urlsafe(16)
        self.tokens.add(token)
        if self.token_as_form:
            return Response(urlencode({"access_token": token, "token_type": "bearer"}),
                            media_type="application/x-www-form-urlencoded")
        return JSONResponse({"access_token": token, "token_type": "Bearer", "expires_in": 3600})

    async def _me(self, request: Request):
        if self.userinfo_token == "query":
            token = request.query_params.get("access_token", "")
        else:
            scheme, _, token = request.headers.get("authorization", "").partition(" ")
            token = token if scheme.lower() == "bearer" else ""
        if token not in self.tokens:
            return JSONResponse({"code": 401}, status_code=401)
        return JSONResponse({"code": 0, "data": {"user": self.user}})
