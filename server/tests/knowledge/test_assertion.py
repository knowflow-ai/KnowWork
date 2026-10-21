import pytest
from joserfc import jwt
from joserfc.jwk import OctKey

from knowwork_server.knowledge.assertion import (
    AssertionRejected,
    AssertionSigner,
    AssertionVerifier,
    MemoryReplayStore,
    key_id,
)
from knowwork_server.knowledge.errors import IdentityUnavailable
from knowwork_server.knowledge.models import Caller

KEY = "s" * 40
ALICE = Caller(workspace_id="ws1", user_id="u-alice", email="alice@corp.com")


def verifier(store=None, key=KEY):
    return AssertionVerifier({key_id(key): key}, replay_store=store or MemoryReplayStore())


def test_assertion_carries_identity_and_expires_in_sixty_seconds():
    token = AssertionSigner(KEY).issue(ALICE, now=1000)
    claims = verifier().verify(token, now=1001)
    assert (claims.email, claims.user_id, claims.workspace_id) == ("alice@corp.com", "u-alice", "ws1")
    assert claims.expires_at == 1060
    assert len(claims.token_id) >= 32


def test_expired_assertion_is_rejected():
    token = AssertionSigner(KEY).issue(ALICE, now=1000)
    with pytest.raises(AssertionRejected) as info:
        verifier().verify(token, now=1066)
    assert info.value.code == "assertion_expired"


def test_replayed_assertion_is_rejected_within_its_lifetime():
    store = MemoryReplayStore()
    token = AssertionSigner(KEY).issue(ALICE, now=1000)
    verifier(store).verify(token, now=1001)
    with pytest.raises(AssertionRejected) as info:
        verifier(store).verify(token, now=1002)
    assert info.value.code == "assertion_replayed"


def test_each_assertion_has_a_fresh_nonce():
    signer = AssertionSigner(KEY)
    store = MemoryReplayStore()
    verifier(store).verify(signer.issue(ALICE, now=1000), now=1000)
    verifier(store).verify(signer.issue(ALICE, now=1000), now=1000)


def test_wrong_key_unknown_kid_and_garbage_are_invalid():
    token = AssertionSigner("x" * 40).issue(ALICE, now=1000)
    for bad in (token, "abc", token.replace(".", "", 1)):
        with pytest.raises(AssertionRejected) as info:
            verifier().verify(bad, now=1001)
        assert info.value.code == "assertion_invalid"


def test_forged_claims_are_invalid():
    key = OctKey.import_key(KEY.encode())
    header = {"alg": "HS256", "kid": key_id(KEY)}
    base = {"iss": "knowwork", "aud": "knowflow", "sub": "u", "email": "a@b.c", "iat": 1000, "exp": 1060,
            "jti": "j" * 32}
    cases = [
        {**base, "aud": "other"},
        {**base, "exp": 1000 + 3600},          # 有效期超过 60 秒
        {**base, "iat": 1500, "exp": 1550},    # 签发时间在未来
        {k: v for k, v in base.items() if k != "email"},
        {**base, "jti": "short"},
    ]
    for claims in cases:
        with pytest.raises(AssertionRejected) as info:
            verifier().verify(jwt.encode(header, claims, key), now=1001)
        assert info.value.code == "assertion_invalid", claims
    other_alg = jwt.encode({"alg": "HS512", "kid": key_id(KEY)}, base, key, algorithms=["HS512"])
    with pytest.raises(AssertionRejected):
        verifier().verify(other_alg, now=1001)


def test_caller_without_email_cannot_get_an_assertion():
    with pytest.raises(IdentityUnavailable):
        AssertionSigner(KEY).issue(Caller(workspace_id="ws1", user_id="u"), now=1000)
    with pytest.raises(ValueError):
        AssertionSigner("short")


def test_replay_store_forgets_expired_nonces():
    store = MemoryReplayStore()
    assert store.use_once("n1", expires_at=1060, now=1000)
    assert not store.use_once("n1", expires_at=1060, now=1001)
    assert store.use_once("n1", expires_at=1200, now=1100)  # 原断言早已过期，不会再被接受
