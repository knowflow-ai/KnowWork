"""测试用的、要 OAuth 授权的 MCP 服务：同一个进程里既是 MCP 服务（工具同 mcp_stub），又是它的授权服务器
（照 MCP 规范的 OAuth 2.1 + PKCE）。

- /.well-known/oauth-protected-resource/mcp：受保护资源元数据，指向本机的授权服务器；
- /.well-known/oauth-authorization-server：授权服务器元数据（dynamic=False 时不给注册地址，只认预先建好的应用）；
- /register：动态注册，发 client_id 与 client_secret（client_secret_basic）；
- /authorize：不弹登录页，直接以 login_as 这个账号签一个授权码跳回 redirect_uri（测试里照着 Location 走）；
- /token：授权码（校验 client、redirect_uri、resource、PKCE）与刷新令牌（轮换，旧的作废）；
- /mcp：只认还有效的访问令牌，否则 401 并在 WWW-Authenticate 里指向资源元数据；记下每次调用是谁的令牌。
"""

import asyncio
import base64
import hashlib
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from .mcp_stub import build_mcp_app

MANUAL_CLIENT = ("kw-manual", "manual-secret-5678")  # 不支持动态注册时，管理员在对方那边建好的应用


@dataclass
class Grant:
    client_id: str
    user: str
    expires_at: float


@dataclass
class OAuthState:
    base: str
    dynamic: bool = True
    anonymous: bool = False  # 不带凭据也能用（登录可选，例如公开文档检索另给登录用户更多额度）
    expires_in: int = 3600
    login_as: str = "someone"
    clients: dict[str, tuple[str, set[str]]] = field(default_factory=dict)  # client_id → (secret, redirect_uris)
    codes: dict[str, dict] = field(default_factory=dict)
    access: dict[str, Grant] = field(default_factory=dict)
    refresh: dict[str, Grant] = field(default_factory=dict)
    refreshes: int = 0
    callers: list[str] = field(default_factory=list)

    def issue(self, client_id: str, user: str) -> dict:
        access, refresh = secrets.token_urlsafe(16), secrets.token_urlsafe(16)
        self.access[access] = Grant(client_id, user, time.time() + self.expires_in)
        self.refresh[refresh] = Grant(client_id, user, time.time() + 86400)
        return {"access_token": access, "token_type": "Bearer", "expires_in": self.expires_in,
                "refresh_token": refresh, "scope": "tickets:read"}

    def drop_access(self, user: str) -> None:
        """对方提前作废了这个人的访问令牌（刷新令牌还有效），或者访问令牌本来就没写过期时间、已经过期。"""
        for token in [t for t, grant in self.access.items() if grant.user == user]:
            del self.access[token]

    def revoke(self, user: str) -> None:
        """对方吊销了这个人的授权：访问令牌与刷新令牌都作废。"""
        for tokens in (self.access, self.refresh):
            for token in [t for t, grant in tokens.items() if grant.user == user]:
                del tokens[token]


def _oauth_error(code: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": code}, status_code=status)


def build_oauth_routes(state: OAuthState) -> Starlette:
    resource = f"{state.base}/mcp"

    async def resource_metadata(_: Request):
        return JSONResponse({"resource": resource, "authorization_servers": [state.base],
                             "scopes_supported": ["tickets:read"]})

    async def server_metadata(_: Request):
        body = {"issuer": state.base, "authorization_endpoint": f"{state.base}/authorize",
                "token_endpoint": f"{state.base}/token", "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
                "code_challenge_methods_supported": ["S256"],
                "token_endpoint_auth_methods_supported": ["client_secret_basic", "none"],
                "authorization_response_iss_parameter_supported": True}
        if state.dynamic:
            body["registration_endpoint"] = f"{state.base}/register"
        return JSONResponse(body)

    async def register(request: Request):
        if not state.dynamic:
            return Response(status_code=404)
        body = await request.json()
        client_id, secret = "dyn-" + secrets.token_hex(4), secrets.token_urlsafe(12)
        state.clients[client_id] = (secret, set(body["redirect_uris"]))
        return JSONResponse({**body, "client_id": client_id, "client_secret": secret,
                             "token_endpoint_auth_method": "client_secret_basic"}, status_code=201)

    async def authorize(request: Request):
        q = request.query_params
        client = state.clients.get(q.get("client_id", ""))
        if client is None or q.get("redirect_uri") not in client[1] or q.get("code_challenge_method") != "S256" \
                or q.get("resource") != resource:
            return Response("bad authorize request", status_code=400)
        code = secrets.token_urlsafe(12)
        state.codes[code] = {"client_id": q["client_id"], "challenge": q["code_challenge"],
                             "redirect_uri": q["redirect_uri"], "user": state.login_as}
        return RedirectResponse(f"{q['redirect_uri']}?" + urlencode({"code": code, "state": q["state"],
                                                                      "iss": state.base}), status_code=302)

    def client_of(request: Request, form) -> str | None:
        header = request.headers.get("authorization", "")
        if header.startswith("Basic "):
            client_id, _, secret = base64.b64decode(header[6:]).decode().partition(":")
        else:
            client_id, secret = form.get("client_id", ""), form.get("client_secret", "")
        known = state.clients.get(client_id)
        return client_id if known is not None and secrets.compare_digest(known[0], secret) else None

    async def token(request: Request):
        form = await request.form()
        client_id = client_of(request, form)
        if client_id is None:
            return _oauth_error("invalid_client", 401)
        if form.get("resource") != resource:
            return _oauth_error("invalid_target")
        if form.get("grant_type") == "authorization_code":
            issued = state.codes.pop(form.get("code", ""), None)
            verifier = form.get("code_verifier", "")
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
            if issued is None or issued["client_id"] != client_id or issued["redirect_uri"] != form.get("redirect_uri") \
                    or issued["challenge"] != challenge:
                return _oauth_error("invalid_grant")
            return JSONResponse(state.issue(client_id, issued["user"]))
        if form.get("grant_type") == "refresh_token":
            grant = state.refresh.pop(form.get("refresh_token", ""), None)
            if grant is None or grant.client_id != client_id:
                return _oauth_error("invalid_grant")
            state.refreshes += 1
            await asyncio.sleep(0.05)  # 慢一点，让并发的调用撞上同一次刷新
            return JSONResponse(state.issue(client_id, grant.user))
        return _oauth_error("unsupported_grant_type")

    return Starlette(routes=[
        Route("/.well-known/oauth-protected-resource/mcp", resource_metadata),
        Route("/.well-known/oauth-authorization-server", server_metadata),
        Route("/register", register, methods=["POST"]),
        Route("/authorize", authorize),
        Route("/token", token, methods=["POST"]),
    ])


def build_app(state: OAuthState, calls: list):
    mcp = build_mcp_app(calls, token="")
    routes = build_oauth_routes(state)
    challenge = f'Bearer resource_metadata="{state.base}/.well-known/oauth-protected-resource/mcp"'.encode()

    async def app(scope, receive, send):
        if scope["type"] == "lifespan" or scope["path"].startswith("/mcp"):
            if scope["type"] == "http":
                header = dict(scope["headers"]).get(b"authorization", b"").decode()
                grant = state.access.get(header.removeprefix("Bearer "))
                if not header and state.anonymous:
                    await mcp(scope, receive, send)
                    return
                if grant is None or grant.expires_at < time.time():
                    await send({"type": "http.response.start", "status": 401,
                                "headers": [(b"www-authenticate", challenge), (b"content-type", b"text/plain")]})
                    await send({"type": "http.response.body", "body": b"unauthorized"})
                    return
                if scope["method"] == "POST":
                    state.callers.append(grant.user)
            await mcp(scope, receive, send)
            return
        await routes(scope, receive, send)

    return app


class McpOAuthStub:
    def __init__(self, port: int, *, dynamic: bool = True) -> None:
        self.port = port
        self.state = OAuthState(base=f"http://127.0.0.1:{port}", dynamic=dynamic)
        self.calls: list[tuple[str, dict]] = []
        config = uvicorn.Config(build_app(self.state, self.calls), host="127.0.0.1", port=port, log_level="warning",
                                lifespan="on")
        self.server = uvicorn.Server(config)
        self.task: asyncio.Task | None = None

    @property
    def url(self) -> str:
        return f"{self.state.base}/mcp"

    def add_manual_client(self, redirect_uri: str) -> None:
        self.state.clients[MANUAL_CLIENT[0]] = (MANUAL_CLIENT[1], {redirect_uri})

    async def __aenter__(self):
        self.task = asyncio.create_task(self.server.serve())
        while not self.server.started:
            await asyncio.sleep(0.02)
        return self

    async def __aexit__(self, *exc):
        self.server.should_exit = True
        await self.task
