"""工具桥的短期令牌（受众 tools）。

和 kernel.tokens 的同一格式（base64url(载荷).base64url(HMAC-SHA256)），但载荷多带会话、会话所属用户与可用工具名，
子密钥按用途单独派生（purpose = tools-callback），与模型网关令牌互不通用。
令牌只说明"签发时这个会话可以用哪些工具"；执行时业务服务仍按注册表重新判定一次。
"""

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass

from ..kernel.tokens import TOOLS_AUDIENCE, TokenError, derive_key

TOKEN_PURPOSE = "tools-callback"
MAX_TOOLS_IN_TOKEN = 128


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@dataclass(frozen=True)
class ToolTokenClaims:
    workspace_id: str
    session_id: str
    user_id: str
    tools: frozenset[str]
    expires_at: int
    token_id: str


class ToolTokenSigner:
    def __init__(self, key: bytes) -> None:
        if len(key) < 32:
            raise ValueError("签名密钥至少 32 字节")
        self._key = key

    @classmethod
    def from_master_key(cls, master_key: str) -> "ToolTokenSigner":
        return cls(derive_key(master_key, TOKEN_PURPOSE))

    def issue(self, *, workspace_id: str, session_id: str, user_id: str, tools: list[str], ttl_seconds: int,
              now: float | None = None) -> str:
        if not user_id:
            raise ValueError("令牌必须带会话所属用户")
        if len(tools) > MAX_TOOLS_IN_TOKEN:
            raise ValueError("可用工具数超过上限")
        issued = int(now if now is not None else time.time())
        payload = {"aud": TOOLS_AUDIENCE, "ws": workspace_id, "sid": session_id, "uid": user_id, "tools": sorted(set(tools)),
                   "exp": issued + ttl_seconds, "jti": secrets.token_hex(8)}
        body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False).encode())
        return f"{body}.{self._sign(body)}"

    def verify(self, token: str, *, now: float | None = None) -> ToolTokenClaims:
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
        if (payload.get("aud") != TOOLS_AUDIENCE or not isinstance(payload.get("exp"), int)
                or payload["exp"] <= current or not isinstance(payload.get("tools"), list)
                or not isinstance(payload.get("uid"), str) or not payload["uid"]):
            raise TokenError("invalid token")
        return ToolTokenClaims(workspace_id=str(payload.get("ws", "")), session_id=str(payload.get("sid", "")),
                               user_id=payload["uid"],
                               tools=frozenset(str(name) for name in payload["tools"]),
                               expires_at=payload["exp"], token_id=str(payload.get("jti", "")))

    def _sign(self, body: str) -> str:
        return _b64(hmac.new(self._key, body.encode(), hashlib.sha256).digest())
