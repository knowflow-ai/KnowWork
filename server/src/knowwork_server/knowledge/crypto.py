"""连接配置里的密钥加密存储：AES-256-GCM（cryptography 库）。

- 密钥由部署主密钥经 HKDF 按用途派生（kernel.tokens.derive_key），不同用途互不共用：知识源用 knowledge-secrets，
  MCP 服务的请求头用 mcp-secrets；
- 每次加密随机 96 位 nonce；附加数据（AAD）绑定「空间/知识源/字段」，密文挪到别处解不开；
- 存储格式：base64url(版本 1 字节 + nonce 12 字节 + 密文与标签)。
接口永不返回明文，只返回「已配置」与末四位（mask）。
"""

import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..kernel.tokens import derive_key

PURPOSE = "knowledge-secrets"
_VERSION = b"\x01"
_NONCE_BYTES = 12


class SecretError(Exception):
    """密文无法解开：格式错、被篡改、上下文不符或密钥不对。不区分具体原因。"""


class SecretBox:
    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("AES-256-GCM 需要 32 字节密钥")
        self._aead = AESGCM(key)

    @classmethod
    def from_master_key(cls, master_key: str, purpose: str = PURPOSE) -> "SecretBox":
        return cls(derive_key(master_key, purpose))

    def seal(self, plaintext: str, *, context: str) -> str:
        nonce = os.urandom(_NONCE_BYTES)
        sealed = self._aead.encrypt(nonce, plaintext.encode(), context.encode())
        return base64.urlsafe_b64encode(_VERSION + nonce + sealed).decode()

    def open(self, token: str, *, context: str) -> str:
        try:
            raw = base64.urlsafe_b64decode(token.encode())
        except (binascii.Error, ValueError) as error:
            raise SecretError("invalid secret") from error
        if len(raw) <= 1 + _NONCE_BYTES or raw[:1] != _VERSION:
            raise SecretError("invalid secret")
        nonce, sealed = raw[1:1 + _NONCE_BYTES], raw[1 + _NONCE_BYTES:]
        try:
            return self._aead.decrypt(nonce, sealed, context.encode()).decode()
        except (InvalidTag, UnicodeDecodeError) as error:
            raise SecretError("invalid secret") from error


def mask(secret: str) -> str:
    """末四位；太短的密钥一位都不露。"""
    return secret[-4:] if len(secret) >= 8 else ""
