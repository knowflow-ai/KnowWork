"""MCP 服务的 OAuth 授权：MCP 规范里的授权（OAuth 2.1 + PKCE），只管和对方授权服务器打交道，不碰数据库。

- 发现：先不带凭据请求一次 MCP 地址，从 401 的 WWW-Authenticate 里取受保护资源元数据地址（RFC 9728），没有就按约定路径找；
  再按元数据里的授权服务器取授权服务器元数据（RFC 8414），issuer 必须对得上。都没有时按 2025-03-26 版规范的老路径，
  在 MCP 服务同源找授权服务器元数据。地址列表、元数据解析、issuer 校验都用官方 SDK（mcp.client.auth）里的函数；
- 注册：对方给了注册地址就动态注册（RFC 7591），否则用管理员在对方那边建好的应用；
- 授权、换令牌、刷新都带 resource（RFC 8707），令牌只对这个服务有效；刷新时对方没给新的刷新令牌就沿用旧的；
- SDK 的 OAuthClientProvider 是给桌面客户端一口气走完的（本机起回调、等人登录），网页要拆成「跳过去」「回调回来」
  两次请求，所以流程在 oauth_login.py 里自己编排，这里只提供每一步；
- 授权服务器的地址必须是 https（本机地址放行，开发与测试用）；不跟随重定向，凭据不会被带到别的主机。
"""

import base64
import ipaddress
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

import httpx2
from mcp.client.auth.exceptions import OAuthFlowError, OAuthRegistrationError, OAuthTokenError
from mcp.client.auth.oauth2 import PKCEParameters, check_registration_usable
from mcp.client.auth.utils import (
    build_oauth_authorization_server_metadata_discovery_urls,
    build_protected_resource_metadata_discovery_urls,
    create_client_registration_request,
    create_oauth_metadata_request,
    extract_resource_metadata_from_www_auth,
    extract_scope_from_www_auth,
    get_client_metadata_scopes,
    handle_auth_metadata_response,
    handle_protected_resource_response,
    handle_registration_response,
    issuers_match,
    validate_authorization_response_iss,
    validate_metadata_issuer,
)
from mcp.shared.auth import OAuthClientMetadata, OAuthMetadata, OAuthToken, ProtectedResourceMetadata
from mcp.shared.auth_utils import check_resource_allowed, resource_url_from_server_url
from pydantic import AnyUrl, ValidationError

TIMEOUT_SECONDS = 15.0
MAX_ERROR_TEXT = 300
CLIENT_NAME = "KnowWork"
GRANT_TYPES = ["authorization_code", "refresh_token"]
LOOPBACK_NAMES = frozenset({"localhost"})
# 探测 MCP 地址时发的 initialize：对方要登录时会回 401 并在 WWW-Authenticate 里指向资源元数据
PROBE_BODY = {"jsonrpc": "2.0", "id": 0, "method": "initialize",
              "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                         "clientInfo": {"name": CLIENT_NAME, "version": "1"}}}


class OAuthFailure(Exception):
    """登录流程里的失败。message 给管理员或成员看；rejected 表示对方明确拒绝了凭据（授权码、刷新令牌无效），
    这时这个人的连接要标成失效；其余（连不上、对方出错）不动连接。"""

    def __init__(self, message: str, *, rejected: bool = False) -> None:
        super().__init__(message)
        self.rejected = rejected


@dataclass(frozen=True)
class AuthServer:
    """发现结果，存进 mcp_server.auth_config（不含密钥）。"""

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str | None
    resource: str | None  # None：对方没有受保护资源元数据（老规范），不带 resource
    scope: str | None  # 对方建议的 scope；管理员可以另填
    iss_supported: bool  # 对方声明了在回调里带 iss（RFC 9207），回调没带就拒绝
    token_auth_methods: tuple[str, ...] | None

    def dump(self) -> dict[str, Any]:
        return {**asdict(self), "token_auth_methods": list(self.token_auth_methods or ()) or None}

    @classmethod
    def load(cls, data: dict[str, Any]) -> "AuthServer":
        methods = data.get("token_auth_methods")
        return cls(issuer=str(data["issuer"]), authorization_endpoint=str(data["authorization_endpoint"]),
                   token_endpoint=str(data["token_endpoint"]), registration_endpoint=data.get("registration_endpoint"),
                   resource=data.get("resource"), scope=data.get("scope"),
                   iss_supported=bool(data.get("iss_supported")),
                   token_auth_methods=tuple(methods) if methods else None)


@dataclass(frozen=True)
class Inspection:
    """登记前探一次的结果。challenged：不带凭据请求时对方回了 401、403，要凭据；server：对方声明的 OAuth 授权服务器。"""

    server: AuthServer | None
    challenged: bool


@dataclass(frozen=True)
class OAuthClient:
    """我们在对方那边的 client：动态注册拿到的，或管理员填的。加密存在 mcp_server.oauth_client_ciphertext。"""

    client_id: str
    client_secret: str | None
    auth_method: str  # none | client_secret_basic | client_secret_post
    dynamic: bool

    def dump(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def load(cls, text: str) -> "OAuthClient":
        data = json.loads(text)
        return cls(client_id=str(data["client_id"]), client_secret=data.get("client_secret"),
                   auth_method=str(data.get("auth_method") or "none"), dynamic=bool(data.get("dynamic")))


@dataclass(frozen=True)
class Tokens:
    access_token: str
    refresh_token: str | None
    expires_at: datetime | None
    scope: str | None


def manual_client(server: AuthServer, client_id: str, client_secret: str | None) -> OAuthClient:
    """管理员在对方那边建的应用：有 secret 时按对方声明的方式带（没声明按 RFC 6749 必须支持的 basic）。"""
    if not client_secret:
        return OAuthClient(client_id=client_id, client_secret=None, auth_method="none", dynamic=False)
    methods = server.token_auth_methods or ()
    method = "client_secret_post" if "client_secret_post" in methods and "client_secret_basic" not in methods \
        else "client_secret_basic"
    return OAuthClient(client_id=client_id, client_secret=client_secret, auth_method=method, dynamic=False)


def new_pkce() -> PKCEParameters:
    return PKCEParameters.generate()


def _short(text: str) -> str:
    return text.strip().replace("\n", " ")[:MAX_ERROR_TEXT]


def _require_secure(url: str, what: str) -> str:
    """授权服务器的地址只认 https；本机地址放行（开发与测试）。"""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "https" and host:
        return url
    if parsed.scheme == "http" and (host in LOOPBACK_NAMES or _loopback(host)):
        return url
    raise OAuthFailure(f"{what}不是 https 地址（{_short(url)}），为了令牌安全不接")


def _origin(url: str) -> str:
    """老规范没有资源元数据时，授权服务器就是 MCP 服务的同源。"""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _with_params(url: str, params: dict[str, str]) -> str:
    """授权地址本身可能带查询参数，合并而不是覆盖。"""
    parsed = urlparse(url)
    merged = [*parse_qsl(parsed.query, keep_blank_values=True), *params.items()]
    return urlunparse(parsed._replace(query=urlencode(merged)))


def _token_error(response: httpx2.Response) -> OAuthFailure:
    """令牌端点的错误：invalid_grant（授权码或刷新令牌无效、过期、被吊销）算对方拒绝。"""
    try:
        body = response.json()
    except (ValueError, json.JSONDecodeError):
        body = {}
    code = str(body.get("error") or "") if isinstance(body, dict) else ""
    detail = str(body.get("error_description") or "") if isinstance(body, dict) else ""
    reason = _short(f"{code} {detail}".strip()) or f"HTTP {response.status_code}"
    return OAuthFailure(f"对方没有发放令牌：{reason}", rejected=code in ("invalid_grant", "invalid_client")
                        or response.status_code == 401)


class OAuthProtocol:
    def __init__(self, transport: httpx2.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    def _http(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(timeout=TIMEOUT_SECONDS, follow_redirects=False, transport=self._transport)

    async def discover(self, server_url: str) -> AuthServer | None:
        """对方要求登录时返回授权服务器；没声明 OAuth 返回 None；连不上、元数据不对抛 OAuthFailure。"""
        return (await self.inspect(server_url)).server

    async def inspect(self, server_url: str) -> Inspection:
        """不带凭据请求一次：要不要凭据、是不是 OAuth。"""
        try:
            async with self._http() as http:
                challenge = await http.post(server_url, json=PROBE_BODY,
                                            headers={"Accept": "application/json, text/event-stream"})
                challenged = challenge.status_code in (401, 403)
                return Inspection(server=await self._discover(http, server_url, challenge, challenged),
                                  challenged=challenged)
        except httpx2.HTTPError as error:
            raise OAuthFailure(f"连不上对方的授权服务：{type(error).__name__}") from error
        except (OAuthFlowError, ValidationError) as error:
            raise OAuthFailure(f"对方的 OAuth 元数据不对：{_short(str(error))}") from error

    async def _discover(self, http: httpx2.AsyncClient, server_url: str, challenge: httpx2.Response,
                        challenged: bool) -> AuthServer | None:
        metadata_url = extract_resource_metadata_from_www_auth(challenge) if challenged else None
        www_scope = extract_scope_from_www_auth(challenge) if challenged else None
        prm = None
        for url in build_protected_resource_metadata_discovery_urls(metadata_url, server_url):
            prm = await handle_protected_resource_response(await http.send(create_oauth_metadata_request(url)))
            if prm is not None:
                break
        if prm is None and not challenged:
            return None  # 不要求登录，也没声明受保护资源：不是 OAuth 服务
        resource = self._resource(server_url, prm)
        issuer = str(prm.authorization_servers[0]) if prm else None
        asm = await self._metadata(http, issuer, server_url, issuer or _origin(server_url))
        if asm is None:
            if prm is None:
                return None  # 要凭据但不是 OAuth（例如只认个人令牌）
            raise OAuthFailure("对方声明了要 OAuth 授权，但找不到它的授权服务器元数据")
        _require_secure(str(asm.authorization_endpoint), "对方的授权地址")
        _require_secure(str(asm.token_endpoint), "对方的令牌地址")
        if asm.registration_endpoint is not None:
            _require_secure(str(asm.registration_endpoint), "对方的注册地址")
        scope = get_client_metadata_scopes(www_scope, prm, asm, GRANT_TYPES)
        return AuthServer(issuer=str(asm.issuer), authorization_endpoint=str(asm.authorization_endpoint),
                          token_endpoint=str(asm.token_endpoint),
                          registration_endpoint=str(asm.registration_endpoint) if asm.registration_endpoint else None,
                          resource=resource, scope=scope,
                          iss_supported=bool(asm.authorization_response_iss_parameter_supported),
                          token_auth_methods=tuple(asm.token_endpoint_auth_methods_supported or ()) or None)

    @staticmethod
    def _resource(server_url: str, prm: ProtectedResourceMetadata | None) -> str | None:
        if prm is None:
            return None
        default = resource_url_from_server_url(server_url)
        declared = str(prm.resource)
        if not check_resource_allowed(requested_resource=default, configured_resource=declared):
            raise OAuthFailure(f"对方声明的资源 {_short(declared)} 和登记的地址对不上")
        return declared

    @staticmethod
    async def _metadata(http: httpx2.AsyncClient, issuer: str | None, server_url: str,
                        expected: str) -> OAuthMetadata | None:
        if issuer is not None:
            _require_secure(issuer, "对方的授权服务器")
        for url in build_oauth_authorization_server_metadata_discovery_urls(issuer, server_url):
            ok, asm = await handle_auth_metadata_response(await http.send(create_oauth_metadata_request(url)))
            if not ok:
                raise OAuthFailure("对方的授权服务器出错了，请稍后再试")
            if asm is None:
                continue
            if issuer is None and issuers_match(str(asm.issuer), expected):
                expected = str(asm.issuer)  # 老路径：根 issuer 带不带结尾斜杠算同一个
            validate_metadata_issuer(asm, expected)
            return asm
        return None

    async def register(self, server: AuthServer, redirect_uri: str) -> OAuthClient:
        if server.registration_endpoint is None:
            raise OAuthFailure("对方不支持自动注册")
        metadata = OAuthClientMetadata(redirect_uris=[AnyUrl(redirect_uri)], client_name=CLIENT_NAME,
                                       grant_types=GRANT_TYPES, response_types=["code"], scope=server.scope,
                                       token_endpoint_auth_method=self._registration_method(server),
                                       application_type="web")
        asm = OAuthMetadata(issuer=server.issuer, authorization_endpoint=server.authorization_endpoint,
                            token_endpoint=server.token_endpoint, registration_endpoint=server.registration_endpoint)
        try:
            async with self._http() as http:
                response = await http.send(create_client_registration_request(asm, metadata, server.issuer))
                info = await handle_registration_response(response)
            check_registration_usable(info)
        except httpx2.HTTPError as error:
            raise OAuthFailure(f"连不上对方的注册地址：{type(error).__name__}") from error
        except OAuthRegistrationError as error:
            raise OAuthFailure(f"自动注册没有成功：{_short(str(error))}") from error
        # 对方没写认证方式时：发了 secret 按 RFC 7591 的默认 basic，没发就是 none
        default = "client_secret_basic" if info.client_secret else "none"
        return OAuthClient(client_id=info.client_id, client_secret=info.client_secret,
                           auth_method=info.token_endpoint_auth_method or default, dynamic=True)

    @staticmethod
    def _registration_method(server: AuthServer) -> str:
        """我们是服务端的机密客户端：对方支持就要 client secret（basic 优先），否则 none（只靠 PKCE）。"""
        methods = server.token_auth_methods
        if methods is None or "client_secret_basic" in methods:
            return "client_secret_basic"
        if "client_secret_post" in methods:
            return "client_secret_post"
        return "none"

    @staticmethod
    def authorize_url(server: AuthServer, client: OAuthClient, *, redirect_uri: str, state: str, challenge: str,
                      scope: str | None) -> str:
        params = {"response_type": "code", "client_id": client.client_id, "redirect_uri": redirect_uri,
                  "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}
        if server.resource:
            params["resource"] = server.resource
        if scope:
            params["scope"] = scope
            if "offline_access" in scope.split():
                params["prompt"] = "consent"  # OIDC 要求要 offline_access 时带上
        return _with_params(server.authorization_endpoint, params)

    @staticmethod
    def check_iss(server: AuthServer, iss: str | None) -> None:
        """回调里的 iss（RFC 9207）：带了就必须是这个授权服务器；对方声明过要带却没带也拒绝。"""
        asm = OAuthMetadata(issuer=server.issuer, authorization_endpoint=server.authorization_endpoint,
                            token_endpoint=server.token_endpoint,
                            authorization_response_iss_parameter_supported=server.iss_supported)
        try:
            validate_authorization_response_iss(iss, asm)
        except OAuthFlowError as error:
            raise OAuthFailure("回调来自别的授权服务器") from error

    async def exchange(self, server: AuthServer, client: OAuthClient, *, code: str, verifier: str,
                       redirect_uri: str) -> Tokens:
        data = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
                "client_id": client.client_id, "code_verifier": verifier}
        return await self._token(server, client, data, previous=None)

    async def refresh(self, server: AuthServer, client: OAuthClient, refresh_token: str) -> Tokens:
        data = {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client.client_id}
        return await self._token(server, client, data, previous=refresh_token)

    async def _token(self, server: AuthServer, client: OAuthClient, data: dict[str, str], *,
                     previous: str | None) -> Tokens:
        if server.resource:
            data["resource"] = server.resource
        headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
        if client.auth_method == "client_secret_basic" and client.client_secret:
            pair = f"{quote(client.client_id, safe='')}:{quote(client.client_secret, safe='')}"
            headers["Authorization"] = "Basic " + base64.b64encode(pair.encode()).decode()
        elif client.auth_method == "client_secret_post" and client.client_secret:
            data["client_secret"] = client.client_secret
        try:
            async with self._http() as http:
                response = await http.post(_require_secure(server.token_endpoint, "对方的令牌地址"), data=data,
                                           headers=headers)
                if response.status_code not in (200, 201):
                    raise _token_error(response)
                token = OAuthToken.model_validate_json(await response.aread())
        except httpx2.HTTPError as error:
            raise OAuthFailure(f"连不上对方的令牌地址：{type(error).__name__}") from error
        except (ValidationError, OAuthTokenError) as error:
            raise OAuthFailure(f"对方返回的令牌格式不对：{_short(str(error))}") from error
        expires_at = datetime.now(UTC) + timedelta(seconds=int(token.expires_in)) if token.expires_in else None
        return Tokens(access_token=token.access_token, refresh_token=token.refresh_token or previous,
                      expires_at=expires_at, scope=token.scope)
