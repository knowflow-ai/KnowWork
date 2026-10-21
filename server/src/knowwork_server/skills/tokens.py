"""技能提供方的短期令牌（受众 skills）。

格式与 kernel.tokens、tools.tokens 相同：base64url(载荷 JSON).base64url(HMAC-SHA256)；子密钥由部署主密钥
经 HKDF 按用途 skills-callback 单独派生，与模型网关、工具桥的令牌互不通用。
载荷只带工作空间、会话与用户，不带技能清单：宿主拉清单、拉内容时，业务服务按此刻的授权重新编译，
权限收回立即生效，令牌也不会随技能数量变长。
"""

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass

from ..kernel.tokens import TokenError, derive_key

SKILLS_AUDIENCE = "skills"
TOKEN_PURPOSE = "skills-callback"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@dataclass(frozen=True)
class SkillTokenClaims:
    workspace_id: str
    session_id: str
    user_id: str
    expires_at: int
    token_id: str


class SkillTokenSigner:
    def __init__(self, key: bytes) -> None:
        if len(key) < 32:
            raise ValueError("签名密钥至少 32 字节")
        self._key = key

    @classmethod
    def from_master_key(cls, master_key: str) -> "SkillTokenSigner":
        if not master_key:
            raise ValueError("master key is required")
        return cls(derive_key(master_key, TOKEN_PURPOSE))

    def issue(self, *, workspace_id: str, session_id: str, user_id: str, ttl_seconds: int,
              now: float | None = None) -> str:
        if not (workspace_id and session_id and user_id) or ttl_seconds <= 0:
            raise ValueError("workspace_id、session_id、user_id 与正的有效期都是必需的")
        issued = int(now if now is not None else time.time())
        payload = {"aud": SKILLS_AUDIENCE, "ws": workspace_id, "sid": session_id, "uid": user_id,
                   "exp": issued + ttl_seconds, "jti": secrets.token_hex(8)}
        body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False).encode())
        return f"{body}.{self._sign(body)}"

    def verify(self, token: str, *, now: float | None = None) -> SkillTokenClaims:
        try:
            body, signature = token.split(".", 1)
        except (ValueError, AttributeError) as error:
            raise TokenError("invalid token") from error
        if not isinstance(signature, str) or not signature.isascii() or not hmac.compare_digest(
                self._sign(body), signature):
            raise TokenError("invalid token")
        try:
            payload = json.loads(_unb64(body))
        except (ValueError, UnicodeDecodeError) as error:
            raise TokenError("invalid token") from error
        current = int(now if now is not None else time.time())
        fields = [payload.get(key) for key in ("ws", "sid", "uid")] if isinstance(payload, dict) else [None]
        if (not isinstance(payload, dict) or payload.get("aud") != SKILLS_AUDIENCE
                or not isinstance(payload.get("exp"), int) or payload["exp"] <= current
                or not all(isinstance(value, str) and value for value in fields)):
            raise TokenError("invalid token")
        return SkillTokenClaims(workspace_id=payload["ws"], session_id=payload["sid"], user_id=payload["uid"],
                                expires_at=payload["exp"], token_id=str(payload.get("jti", "")))

    def _sign(self, body: str) -> str:
        return _b64(hmac.new(self._key, body.encode(), hashlib.sha256).digest())
