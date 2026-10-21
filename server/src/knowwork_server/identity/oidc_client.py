"""OIDC 依赖方的协议部分：发现文档、授权地址（授权码 + PKCE S256）、换令牌、校验 ID Token。

协议参数用 Authlib 的 RFC 6749 / 7636 实现生成，JWT 用 Authlib 生态的 joserfc 校验：
签名算法显式指定、issuer 与 aud 与 exp 必校验、多受众时 azp 必须是本客户端。
"""

import time

import httpx
from authlib.oauth2.rfc6749.parameters import prepare_grant_uri
from authlib.oauth2.rfc7636 import create_s256_code_challenge
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet

from .config import OidcConfig
from .errors import OidcFailure

METADATA_TTL_SECONDS = 3600


class OidcClient:
    def __init__(self, config: OidcConfig, http: httpx.AsyncClient) -> None:
        self._config = config
        self._http = http
        self._metadata: dict | None = None
        self._metadata_at = 0.0
        self._keys: KeySet | None = None

    @property
    def config(self) -> OidcConfig:
        return self._config

    async def _get_json(self, url: str) -> dict:
        try:
            response = await self._http.get(url, timeout=10)
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise OidcFailure("身份提供方不可用") from error
        if not isinstance(body, dict):
            raise OidcFailure("身份提供方响应格式不正确")
        return body

    async def metadata(self) -> dict:
        if self._metadata is None or time.monotonic() - self._metadata_at > METADATA_TTL_SECONDS:
            url = self._config.issuer.rstrip("/") + "/.well-known/openid-configuration"
            body = await self._get_json(url)
            if body.get("issuer") != self._config.issuer:
                raise OidcFailure("发现文档的 issuer 与配置不一致")
            for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
                if not isinstance(body.get(field), str):
                    raise OidcFailure("发现文档缺少必要字段")
            self._metadata, self._metadata_at, self._keys = body, time.monotonic(), None
        return self._metadata

    async def authorization_url(self, *, state: str, nonce: str, code_verifier: str) -> str:
        metadata = await self.metadata()
        return prepare_grant_uri(
            metadata["authorization_endpoint"], self._config.client_id, "code",
            redirect_uri=self._config.redirect_uri, scope=" ".join(self._config.scopes), state=state,
            nonce=nonce, code_challenge=create_s256_code_challenge(code_verifier), code_challenge_method="S256",
        )

    async def exchange_code(self, *, code: str, code_verifier: str) -> str:
        """用授权码换令牌，返回未校验的 ID Token。"""
        metadata = await self.metadata()
        form = {"grant_type": "authorization_code", "code": code, "redirect_uri": self._config.redirect_uri,
                "code_verifier": code_verifier}
        try:
            response = await self._http.post(metadata["token_endpoint"], data=form, timeout=10,
                                             auth=(self._config.client_id, self._config.client_secret))
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise OidcFailure("换取令牌失败") from error
        id_token = body.get("id_token") if isinstance(body, dict) else None
        if not isinstance(id_token, str):
            raise OidcFailure("令牌响应缺少 id_token")
        return id_token

    async def _key_set(self, *, refresh: bool) -> KeySet:
        if self._keys is None or refresh:
            metadata = await self.metadata()
            body = await self._get_json(metadata["jwks_uri"])
            try:
                self._keys = KeySet.import_key_set(body)
            except (JoseError, ValueError, KeyError, TypeError) as error:
                raise OidcFailure("JWKS 格式不正确") from error
        return self._keys

    async def validate_id_token(self, id_token: str) -> dict:
        """校验签名（只接受配置的算法）、iss、aud、exp、iat、sub、nonce 存在；nonce 值由调用方比对。"""
        token = await self._decode(id_token)
        claims = dict(token.claims)
        registry = jwt.JWTClaimsRegistry(
            leeway=self._config.leeway_seconds,
            iss={"essential": True, "value": self._config.issuer},
            aud={"essential": True, "value": self._config.client_id},
            exp={"essential": True}, iat={"essential": True}, sub={"essential": True}, nonce={"essential": True},
        )
        try:
            registry.validate(claims)
        except JoseError as error:
            raise OidcFailure("ID Token 声明校验失败") from error
        audience = claims.get("aud")
        if isinstance(audience, list) and len(audience) > 1 and claims.get("azp") != self._config.client_id:
            raise OidcFailure("ID Token 的 azp 不是本客户端")
        return claims

    async def _decode(self, id_token: str) -> jwt.Token:
        algorithms = list(self._config.algorithms)
        for refresh in (False, True):  # 签名对不上时刷新一次 JWKS，兼容提供方轮换密钥
            keys = await self._key_set(refresh=refresh)
            try:
                return jwt.decode(id_token, keys, algorithms=algorithms)
            except JoseError as error:
                last_error = error
            except ValueError as error:
                raise OidcFailure("ID Token 格式不正确") from error
        raise OidcFailure("ID Token 签名校验失败") from last_error
