"""厂商密钥加密：cryptography 的 AES-256-GCM。

- 子密钥由部署主密钥经 kernel.tokens.derive_key 按用途「model-provider-secret」派生，不与其他用途共用；
- 每次加密用 12 字节随机 nonce；附加数据（AAD）绑定工作空间与厂商 id，密文挪到别的行上解不开；
- 存储格式：v1:base64url(nonce || 密文含标签)。
"""

import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..kernel.tokens import derive_key

PURPOSE = "model-provider-secret"
_VERSION = "v1"
_NONCE_BYTES = 12


class SecretError(Exception):
    """密文无法解开（被篡改、换了主密钥或挪了行）。对外不说具体原因。"""


def _aad(workspace_id: str, provider_id: str) -> bytes:
    return f"model_provider:{workspace_id}:{provider_id}".encode()


class SecretBox:
    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("AES-256-GCM 需要 32 字节密钥")
        self._aead = AESGCM(key)

    @classmethod
    def from_master_key(cls, master_key: str) -> "SecretBox":
        return cls(derive_key(master_key, PURPOSE))

    def encrypt(self, plaintext: str, *, workspace_id: str, provider_id: str) -> str:
        nonce = os.urandom(_NONCE_BYTES)
        sealed = self._aead.encrypt(nonce, plaintext.encode(), _aad(workspace_id, provider_id))
        return f"{_VERSION}:{base64.urlsafe_b64encode(nonce + sealed).decode()}"

    def decrypt(self, token: str, *, workspace_id: str, provider_id: str) -> str:
        version, sep, body = (token or "").partition(":")
        if version != _VERSION or not sep:
            raise SecretError("invalid secret")
        try:
            raw = base64.urlsafe_b64decode(body)
            plain = self._aead.decrypt(raw[:_NONCE_BYTES], raw[_NONCE_BYTES:], _aad(workspace_id, provider_id))
            return plain.decode()
        except (InvalidTag, ValueError, UnicodeDecodeError) as error:
            raise SecretError("invalid secret") from error


def last4(secret: str) -> str:
    """只回显末四位；太短的密钥一位都不回显，免得等于回显全文。"""
    return secret[-4:] if len(secret) >= 12 else ""
