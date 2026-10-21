import pytest

from knowwork_server.kernel.tokens import TokenError, TokenSigner, derive_key

KEY = derive_key("m" * 40, "host-callback")


def test_issue_and_verify():
    signer = TokenSigner(KEY)
    token = signer.issue(audience="model", workspace_id="ws1", ttl_seconds=60, now=1000)
    claims = signer.verify(token, audience="model", now=1030)
    assert claims.workspace_id == "ws1"


@pytest.mark.parametrize("mutate", [
    lambda t: t[:-2] + ("AA" if not t.endswith("AA") else "BB"),
    lambda t: "x" + t[1:],
    lambda t: t.split(".")[0],
])
def test_tampered_tokens_rejected(mutate):
    signer = TokenSigner(KEY)
    token = signer.issue(audience="model", workspace_id="ws1", ttl_seconds=60, now=1000)
    with pytest.raises(TokenError):
        signer.verify(mutate(token), audience="model", now=1001)


def test_wrong_audience_and_expiry_rejected():
    signer = TokenSigner(KEY)
    token = signer.issue(audience="model", workspace_id="ws1", ttl_seconds=60, now=1000)
    with pytest.raises(TokenError):
        signer.verify(token, audience="tools", now=1001)
    with pytest.raises(TokenError):
        signer.verify(token, audience="model", now=1060)


def test_purpose_keys_differ():
    assert derive_key("m" * 40, "host-callback") != derive_key("m" * 40, "session-cookie")
    other = TokenSigner(derive_key("m" * 40, "other"))
    token = TokenSigner(KEY).issue(audience="model", workspace_id="ws1", ttl_seconds=60)
    with pytest.raises(TokenError):
        other.verify(token, audience="model")
