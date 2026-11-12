"""网页嵌入的会话令牌：嵌入页换到它，之后的对话接口都带着它（identity/embed.py 认）。

嵌入页拿访客 id（公开访问）或接入方签的令牌（认证访问）换到它以后只放在内存里，每个请求带
`Authorization: Bearer emb_...`：跨站 iframe 里浏览器不带我们的 Cookie，这是「前端不存令牌」唯一的例外，
只放内存、不写任何浏览器存储、两小时过期（用着就续），页面关了就没了。

格式同工具桥令牌（base64url(载荷).base64url(HMAC-SHA256)），前面加 emb_ 好认；子密钥按用途单独派生，
别的用途的令牌换上前缀也验不过。载荷记下空间、渠道、落到的用户、访客，以及发布令牌的指纹：管理员重置发布令牌后
指纹对不上，已经发出去的一起作废。令牌只说明「签发时这个人从这个渠道进来」，每次用都要再核对渠道还在、
还开着（channels/web.py）。
"""

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass

from ..kernel.tokens import TokenError, derive_key

PREFIX = "emb_"
PURPOSE = "web-embed"
AUDIENCE = "web-embed"
TTL_SECONDS = 2 * 3600
# 访客 id 由嵌入页随机生成（crypto.randomUUID 去掉横线是 32 位），只用来接着上一段对话，不是凭据
VISITOR = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def key_print(web_key: str) -> str:
    """发布令牌的指纹：载荷里只放它，发布令牌本身不进会话令牌。"""
    return hashlib.sha256(web_key.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class EmbedClaims:
    workspace_id: str
    channel_id: str
    user_id: str
    visitor: str | None  # 公开访问的访客；认证访问落到的是本人，没有访客
    key_print: str
    expires_at: int


class EmbedTokens:
    def __init__(self, key: bytes, ttl: int = TTL_SECONDS) -> None:
        if len(key) < 32:
            raise ValueError("签名密钥至少 32 字节")
        self._key = key
        self._ttl = ttl

    @classmethod
    def from_master_key(cls, master_key: str) -> "EmbedTokens":
        return cls(derive_key(master_key, PURPOSE))

    def issue(self, *, workspace_id: str, channel_id: str, user_id: str, visitor: str | None, key_print: str,
              now: float | None = None) -> str:
        if not (workspace_id and channel_id and user_id and key_print):
            raise ValueError("嵌入会话令牌要带空间、渠道、用户与发布令牌指纹")
        if visitor is not None and not VISITOR.fullmatch(visitor):
            raise ValueError("访客 id 格式不对")
        issued = int(now if now is not None else time.time())
        payload = {"aud": AUDIENCE, "ws": workspace_id, "ch": channel_id, "uid": user_id, "vis": visitor,
                   "kp": key_print, "exp": issued + self._ttl, "jti": secrets.token_hex(8)}
        body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
        return f"{PREFIX}{body}.{self._sign(body)}"

    def verify(self, token: str, *, now: float | None = None) -> EmbedClaims:
        if not token.startswith(PREFIX):
            raise TokenError("invalid token")
        body, _, signature = token[len(PREFIX):].partition(".")
        if not signature or not hmac.compare_digest(self._sign(body), signature):
            raise TokenError("invalid token")
        try:
            payload = json.loads(_unb64(body))
        except (ValueError, UnicodeDecodeError) as error:
            raise TokenError("invalid token") from error
        current = int(now if now is not None else time.time())
        fields = ("ws", "ch", "uid", "kp")
        if (not isinstance(payload, dict) or payload.get("aud") != AUDIENCE
                or not isinstance(payload.get("exp"), int) or payload["exp"] <= current
                or not all(isinstance(payload.get(name), str) and payload[name] for name in fields)
                or not (payload.get("vis") is None or isinstance(payload["vis"], str))):
            raise TokenError("invalid token")
        return EmbedClaims(workspace_id=payload["ws"], channel_id=payload["ch"], user_id=payload["uid"],
                           visitor=payload["vis"], key_print=payload["kp"], expires_at=payload["exp"])

    def _sign(self, body: str) -> str:
        return _b64(hmac.new(self._key, body.encode(), hashlib.sha256).digest())
