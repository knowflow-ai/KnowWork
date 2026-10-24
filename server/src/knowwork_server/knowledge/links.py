"""对外接口里的短期链接：检索结果里的图片给带签名的链接，至少 2 小时有效，
调用方（脚本、MCP 客户端）不带登录凭证也能取，但取图时仍按签名里的用户重新判权。

过期时间按 10 分钟取整：同一段时间里给同一个人签同一张图，链接一字不差。对话里每次检索返回都会重新拉出处，
过期时间按秒算的话链接每次都变，回答里的图就会重新加载、闪一下，浏览器缓存也用不上。

签名覆盖：路径、用户、过期时间；用业务服务主密钥派生的专用密钥（换主密钥后旧链接全部失效）。
"""

import hashlib
import hmac
import time
from collections.abc import Callable, Mapping
from urllib.parse import urlencode, urlsplit

TTL_SECONDS = 2 * 3600
EXPIRY_STEP = 600


class LinkSigner:
    def __init__(self, key: bytes, *, clock: Callable[[], float] = time.time) -> None:
        self._key = key
        self._clock = clock

    def _signature(self, path: str, user_id: str, expires: int) -> str:
        message = f"{path}\n{user_id}\n{expires}".encode()
        return hmac.new(self._key, message, hashlib.sha256).hexdigest()

    def sign(self, url: str, user_id: str, *, ttl_seconds: int = TTL_SECONDS) -> str:
        """给站内的相对链接加上签名参数；不是站内路径的原样返回。"""
        parts = urlsplit(url)
        if parts.scheme or parts.netloc or not parts.path.startswith("/api/") or parts.query:
            return url
        expires = -(-(int(self._clock()) + ttl_seconds) // EXPIRY_STEP) * EXPIRY_STEP  # 向上取整，不会短于 ttl
        query = urlencode({"uid": user_id, "exp": expires, "sig": self._signature(parts.path, user_id, expires)})
        return f"{parts.path}?{query}"

    def verify(self, path: str, params: Mapping[str, str]) -> str | None:
        """签名对、没过期时返回签名里的用户，否则 None。"""
        user_id, expires, signature = params.get("uid"), params.get("exp"), params.get("sig")
        if not (user_id and expires and signature and expires.isdigit()):
            return None
        if int(expires) < self._clock():
            return None
        expected = self._signature(path, user_id, int(expires))
        return user_id if hmac.compare_digest(expected, signature) else None
