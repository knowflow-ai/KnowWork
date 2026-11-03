"""通用 OAuth2 登录（不带 OIDC）：授权码（可选 PKCE）→ 访问令牌 → 对方的用户信息接口 → 按配置的路径取 ID、姓名、邮箱。

很多企业自研的统一认证、OA 门户单点登录是这个形态：不给 id_token，用户信息接口的字段各家不一样。
- 授权地址的参数用 Authlib 的 RFC 6749 / 7636 实现生成，同 OIDC；
- 换令牌：客户端凭证放 Basic 头或表单（看对方要哪种）；返回 JSON，也认老系统的表单格式；
- 用户信息：令牌放 Bearer 头或查询参数 access_token；
- 不跟随重定向（httpx 默认），响应超过 1 MB 当失败，凭证不会被带到别的主机；
- 邮箱默认不可信（OAuth2 没有 email_verified），管理员确认邮箱由企业统一分配才勾「邮箱可信」。
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import parse_qsl

import httpx
from authlib.oauth2.rfc6749.parameters import prepare_grant_uri
from authlib.oauth2.rfc7636 import create_s256_code_challenge

from .errors import SsoFailure
from .external import ExternalProfile

MAX_BODY_BYTES = 1024 * 1024
TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class OAuth2Config:
    realm: str                 # 身份域：oauth2:<登录方式 id>
    authorize_url: str
    token_url: str
    userinfo_url: str
    client_id: str
    client_secret: str
    redirect_uri: str
    scopes: tuple[str, ...] = ()
    token_auth: str = "basic"       # basic | post
    pkce: bool = True
    userinfo_token: str = "header"  # header | query
    subject_path: str = "id"
    name_path: str = "name"
    email_path: str = "email"
    email_trusted: bool = False


def pick(data: object, path: str) -> object:
    """按点分路径取值，数字段取数组下标：data.user.id、users.0.mail。取不到返回 None。"""
    current = data
    for key in path.split("."):
        if isinstance(current, Mapping):
            current = current.get(key)
        elif isinstance(current, list) and key.isdigit() and int(key) < len(current):
            current = current[int(key)]
        else:
            return None
    return current


def _text(value: object) -> str | None:
    # ID 常是数字；布尔值也是 int 的子类，不能当 ID
    if isinstance(value, bool):
        return None
    if isinstance(value, int | str) and str(value).strip():
        return str(value).strip()
    return None


def _body(response: httpx.Response) -> bytes:
    if len(response.content) > MAX_BODY_BYTES:
        raise SsoFailure("对方响应过大")
    return response.content


class OAuth2Client:
    def __init__(self, config: OAuth2Config, http: httpx.AsyncClient) -> None:
        self._config = config
        self._http = http

    @property
    def config(self) -> OAuth2Config:
        return self._config

    async def authorization_url(self, *, state: str, nonce: str, code_verifier: str, in_client: bool = False) -> str:
        config = self._config
        extra = {"code_challenge": create_s256_code_challenge(code_verifier), "code_challenge_method": "S256"} \
            if config.pkce else {}
        return prepare_grant_uri(config.authorize_url, config.client_id, "code", redirect_uri=config.redirect_uri,
                                 scope=" ".join(config.scopes) or None, state=state, **extra)

    async def profile(self, *, code: str, code_verifier: str, nonce_hash: str) -> ExternalProfile:
        """OAuth2 没有 nonce，nonce_hash 不用（state 已经防住了登录 CSRF）。"""
        token = await self._exchange(code, code_verifier)
        info = await self._userinfo(token)
        config = self._config
        subject = _text(pick(info, config.subject_path))
        if subject is None:
            raise SsoFailure("用户信息里没有 ID")
        email = _text(pick(info, config.email_path))
        return ExternalProfile(realm=config.realm, subject=subject, name=_text(pick(info, config.name_path)) or "",
                               email=email, email_trusted=config.email_trusted and email is not None)

    async def _exchange(self, code: str, code_verifier: str) -> str:
        config = self._config
        form = {"grant_type": "authorization_code", "code": code, "redirect_uri": config.redirect_uri}
        if config.pkce:
            form["code_verifier"] = code_verifier
        auth = None
        if config.token_auth == "post":
            form |= {"client_id": config.client_id, "client_secret": config.client_secret}
        else:
            auth = (config.client_id, config.client_secret)
        try:
            response = await self._http.post(config.token_url, data=form, auth=auth, timeout=TIMEOUT_SECONDS,
                                             headers={"accept": "application/json"})
            response.raise_for_status()
            raw = _body(response)
        except httpx.HTTPError as error:
            raise SsoFailure("换取令牌失败") from error
        token = _token_from(raw, response.headers.get("content-type", ""))
        if token is None:
            raise SsoFailure("令牌响应缺少 access_token")
        return token

    async def _userinfo(self, token: str) -> object:
        config = self._config
        headers, params = {"accept": "application/json"}, {}
        if config.userinfo_token == "query":
            params["access_token"] = token
        else:
            headers["authorization"] = f"Bearer {token}"
        try:
            response = await self._http.get(config.userinfo_url, headers=headers, params=params,
                                            timeout=TIMEOUT_SECONDS)
            response.raise_for_status()
            return json.loads(_body(response))
        except (httpx.HTTPError, ValueError) as error:
            raise SsoFailure("取用户信息失败") from error


def _token_from(raw: bytes, content_type: str) -> str | None:
    if "application/x-www-form-urlencoded" in content_type or "text/plain" in content_type:
        values = dict(parse_qsl(raw.decode(errors="replace")))
    else:
        try:
            values = json.loads(raw)
        except ValueError:
            return None
    token = values.get("access_token") if isinstance(values, dict) else None
    return token if isinstance(token, str) and token else None
