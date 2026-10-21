"""令牌的生成、摘要与常量时间比较。库里只存摘要，明文只在签发时出现一次。"""

import hashlib
import hmac
import secrets
from collections.abc import Callable
from datetime import UTC, datetime

Clock = Callable[[], datetime]


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def new_id() -> str:
    return secrets.token_hex(16)
