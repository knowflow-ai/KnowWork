"""短期签名令牌：宿主回调业务服务时携带。

- 子密钥由部署主密钥经 HKDF 按用途派生，各用途互不通用；
- 令牌带受众（aud）、工作空间、过期时间；校验时三者都核对；
- 格式：base64url(载荷 JSON).base64url(HMAC-SHA256)。
"""

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# 令牌受众。模型网关的受众常量在 gateway.model_gateway.MODEL_AUDIENCE。
TOOLS_AUDIENCE = "tools"  # 宿主工具桥回调业务服务内部工具接口


class TokenError(Exception):
    """令牌无效：格式、签名、受众或过期不符。对外只说无效，不说具体原因。"""


def derive_key(master_key: str, purpose: str) -> bytes:
    hkdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=b"knowwork", info=f"knowwork:{purpose}".encode())
    return hkdf.derive(master_key.encode())


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@dataclass(frozen=True)
class TokenClaims:
    audience: str
    workspace_id: str
    expires_at: int
    token_id: str


class TokenSigner:
    def __init__(self, key: bytes) -> None:
        if len(key) < 32:
            raise ValueError("签名密钥至少 32 字节")
        self._key = key

    def issue(self, *, audience: str, workspace_id: str, ttl_seconds: int, now: float | None = None) -> str:
        issued = int(now if now is not None else time.time())
        payload = {"aud": audience, "ws": workspace_id, "exp": issued + ttl_seconds, "jti": secrets.token_hex(8)}
        body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
        return f"{body}.{self._sign(body)}"

    def verify(self, token: str, *, audience: str, now: float | None = None) -> TokenClaims:
        try:
            body, signature = token.split(".", 1)
        except ValueError as error:
            raise TokenError("invalid token") from error
        if not hmac.compare_digest(self._sign(body), signature):
            raise TokenError("invalid token")
        try:
            payload = json.loads(_unb64(body))
        except (ValueError, UnicodeDecodeError) as error:
            raise TokenError("invalid token") from error
        current = int(now if now is not None else time.time())
        if payload.get("aud") != audience or not isinstance(payload.get("exp"), int) or payload["exp"] <= current:
            raise TokenError("invalid token")
        return TokenClaims(audience=payload["aud"], workspace_id=str(payload.get("ws", "")),
                           expires_at=payload["exp"], token_id=str(payload.get("jti", "")))

    def _sign(self, body: str) -> str:
        return _b64(hmac.new(self._key, body.encode(), hashlib.sha256).digest())
