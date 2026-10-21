"""厂商密钥：AES-256-GCM，密文绑定空间与厂商，篡改、换钥、挪行都解不开。"""

import pytest

from knowwork_server.kernel.tokens import derive_key
from knowwork_server.models.crypto import PURPOSE, SecretBox, SecretError, last4

BOX = SecretBox.from_master_key("m" * 40)
WHERE = {"workspace_id": "ws1", "provider_id": "mp_1"}


def test_round_trip_and_random_nonce():
    first = BOX.encrypt("sk-secret-value-1234", **WHERE)
    second = BOX.encrypt("sk-secret-value-1234", **WHERE)
    assert first != second and first.startswith("v1:")
    assert "sk-secret" not in first
    assert BOX.decrypt(first, **WHERE) == "sk-secret-value-1234"


def test_key_is_derived_for_its_own_purpose():
    assert SecretBox(derive_key("m" * 40, PURPOSE)).decrypt(BOX.encrypt("x" * 20, **WHERE), **WHERE) == "x" * 20
    other = SecretBox(derive_key("m" * 40, "host-callback"))
    with pytest.raises(SecretError):
        other.decrypt(BOX.encrypt("x" * 20, **WHERE), **WHERE)


@pytest.mark.parametrize("where", [{"workspace_id": "ws2", "provider_id": "mp_1"},
                                   {"workspace_id": "ws1", "provider_id": "mp_2"}])
def test_ciphertext_is_bound_to_its_row(where):
    with pytest.raises(SecretError):
        BOX.decrypt(BOX.encrypt("sk-abcdefgh1234", **WHERE), **where)


@pytest.mark.parametrize("token", ["", "v2:abc", "v1", "v1:!!!", "v1:AAAA"])
def test_malformed_ciphertext(token):
    with pytest.raises(SecretError):
        BOX.decrypt(token, **WHERE)


def test_tampered_ciphertext():
    token = BOX.encrypt("sk-abcdefgh1234", **WHERE)
    flipped = token[:-2] + ("A" if token[-2] != "A" else "B") + token[-1]
    with pytest.raises(SecretError):
        BOX.decrypt(flipped, **WHERE)


def test_key_length_checked():
    with pytest.raises(ValueError):
        SecretBox(b"short")


def test_last4_never_reveals_short_secrets():
    assert last4("sk-0123456789abcd") == "abcd"
    assert last4("short") == ""
