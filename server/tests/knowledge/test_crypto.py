import pytest

from knowwork_server.knowledge.crypto import SecretBox, SecretError, mask

MASTER = "k" * 40


def test_round_trip_and_binding_to_context():
    box = SecretBox.from_master_key(MASTER)
    sealed = box.seal("kf-secret-1234", context="ws1/src1/service_key")
    assert "kf-secret" not in sealed
    assert box.open(sealed, context="ws1/src1/service_key") == "kf-secret-1234"
    with pytest.raises(SecretError):
        box.open(sealed, context="ws2/src1/service_key")  # 密文不能挪到别的空间或知识源


def test_each_seal_uses_a_fresh_nonce_and_tampering_is_detected():
    box = SecretBox.from_master_key(MASTER)
    a = box.seal("same", context="c")
    b = box.seal("same", context="c")
    assert a != b
    tampered = a[:-4] + ("AAAA" if not a.endswith("AAAA") else "BBBB")
    with pytest.raises(SecretError):
        box.open(tampered, context="c")
    with pytest.raises(SecretError):
        box.open("not-base64!!", context="c")


def test_keys_derived_for_other_purposes_cannot_open():
    sealed = SecretBox.from_master_key(MASTER).seal("x", context="c")
    with pytest.raises(SecretError):
        SecretBox.from_master_key("z" * 40).open(sealed, context="c")
    with pytest.raises(ValueError):
        SecretBox(b"short")


def test_mask_only_reveals_last_four():
    assert mask("ragflow-abcdef9876") == "9876"
    assert mask("abc") == ""
