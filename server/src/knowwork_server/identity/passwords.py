"""密码哈希：Argon2id（argon2-cffi 默认参数）。"""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 256

_hasher = PasswordHasher()
# 账号不存在或没有密码时也做一次同等代价的校验，避免靠响应时间枚举账号。
_DUMMY_HASH = _hasher.hash("knowwork-dummy-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        matched = _hasher.verify(password_hash or _DUMMY_HASH, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    return matched and password_hash is not None


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)
