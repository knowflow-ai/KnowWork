"""KnowFlow 身份透传用的短期签名断言。

格式用标准 JWT（JWS Compact，HS256，joserfc 实现），KnowFlow 侧可直接用 PyJWT 校验：
- 头：alg=HS256，kid=服务密钥指纹（便于轮换时并存两把密钥）；
- 载荷：iss=knowwork、aud=knowflow、sub=平台用户 ID、email、ws=工作空间、iat、exp=iat+60、jti=128 位随机串。
每次 HTTP 请求签一个新断言；jti 在有效期内只能用一次。

AssertionVerifier 是 KnowFlow 侧校验步骤的参考实现，测试桩用它扮演 KnowFlow。
"""

import hashlib
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Protocol

from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import OctKey

from .errors import IdentityUnavailable
from .models import Caller

ISSUER = "knowwork"
AUDIENCE = "knowflow"
TTL_SECONDS = 60
ALGORITHM = "HS256"
HEADER_NAME = "X-KnowFlow-Assertion"
MIN_KEY_CHARS = 32
_LEEWAY = 5
_REQUIRED = ("iss", "aud", "sub", "email", "iat", "exp", "jti")


def key_id(service_key: str) -> str:
    return hashlib.sha256(service_key.encode()).hexdigest()[:16]


def _oct(service_key: str) -> OctKey:
    if len(service_key) < MIN_KEY_CHARS:
        raise ValueError(f"服务密钥至少 {MIN_KEY_CHARS} 个字符")
    return OctKey.import_key(service_key.encode())


class AssertionSigner:
    def __init__(self, service_key: str) -> None:
        self._key = _oct(service_key)
        self._kid = key_id(service_key)

    def issue(self, caller: Caller, *, now: float | None = None) -> str:
        if not caller.email:
            raise IdentityUnavailable("你的平台账号没有邮箱，KnowFlow 无法识别你的身份")
        issued = int(now if now is not None else time.time())
        claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": caller.user_id, "email": caller.email.lower(),
                  "ws": caller.workspace_id, "iat": issued, "exp": issued + TTL_SECONDS,
                  "jti": secrets.token_hex(16)}
        return jwt.encode({"alg": ALGORITHM, "kid": self._kid}, claims, self._key, algorithms=[ALGORITHM])


class ReplayStore(Protocol):
    def use_once(self, token_id: str, *, expires_at: int, now: int) -> bool:
        """第一次见到返回 True 并记下，有效期内再见返回 False。生产环境用 Redis SET NX EX。"""
        ...


class MemoryReplayStore:
    """单进程参考实现：记到断言过期为止，过期的条目顺手清掉。"""

    def __init__(self) -> None:
        self._seen: dict[str, int] = {}
        self._lock = threading.Lock()

    def use_once(self, token_id: str, *, expires_at: int, now: int) -> bool:
        with self._lock:
            self._seen = {k: exp for k, exp in self._seen.items() if exp + _LEEWAY > now}
            if token_id in self._seen:
                return False
            self._seen[token_id] = expires_at
            return True


@dataclass(frozen=True)
class AssertionClaims:
    user_id: str
    email: str
    workspace_id: str
    expires_at: int
    token_id: str


class AssertionRejected(Exception):
    """code：assertion_invalid | assertion_expired | assertion_replayed。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class AssertionVerifier:
    def __init__(self, keys: dict[str, str], *, replay_store: ReplayStore) -> None:
        self._keys = {kid: _oct(secret) for kid, secret in keys.items()}
        self._replay = replay_store

    def verify(self, token: str, *, now: float | None = None) -> AssertionClaims:
        current = int(now if now is not None else time.time())
        claims = self._decode(token)
        _check_claims(claims, current)
        if not self._replay.use_once(claims["jti"], expires_at=claims["exp"], now=current):
            raise AssertionRejected("assertion_replayed")
        return AssertionClaims(user_id=claims["sub"], email=claims["email"], workspace_id=str(claims.get("ws", "")),
                               expires_at=claims["exp"], token_id=claims["jti"])

    def _decode(self, token: str) -> dict:
        try:
            decoded = jwt.decode(token, lambda guest: _any_key(guest, self._keys), algorithms=[ALGORITHM])
        except (JoseError, ValueError, TypeError, KeyError) as error:
            raise AssertionRejected("assertion_invalid") from error
        if decoded.header.get("alg") != ALGORITHM:
            raise AssertionRejected("assertion_invalid")
        return dict(decoded.claims)


def _any_key(guest, keys: dict[str, OctKey]) -> OctKey:
    key = keys.get(guest.headers().get("kid", ""))
    if key is None:
        raise KeyError("unknown kid")
    return key


def _check_claims(claims: dict, now: int) -> None:
    if any(name not in claims for name in _REQUIRED):
        raise AssertionRejected("assertion_invalid")
    iat, exp, jti = claims["iat"], claims["exp"], claims["jti"]
    if not (isinstance(iat, int) and isinstance(exp, int) and isinstance(jti, str) and 32 <= len(jti) <= 128):
        raise AssertionRejected("assertion_invalid")
    if claims["iss"] != ISSUER or claims["aud"] != AUDIENCE or not claims["sub"] or not claims["email"]:
        raise AssertionRejected("assertion_invalid")
    if exp - iat > TTL_SECONDS or iat > now + _LEEWAY:
        raise AssertionRejected("assertion_invalid")
    if exp + _LEEWAY <= now:
        raise AssertionRejected("assertion_expired")
