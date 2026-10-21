"""技能令牌：受众 skills，子密钥单独派生，过期与篡改都拒绝。"""

import pytest

from knowwork_server.kernel.tokens import TokenError
from knowwork_server.skills.tokens import SkillTokenSigner
from knowwork_server.tools.tokens import ToolTokenSigner

MASTER = "m" * 40


def test_roundtrip():
    signer = SkillTokenSigner.from_master_key(MASTER)
    claims = signer.verify(signer.issue(workspace_id="ws1", session_id="s1", user_id="alice", ttl_seconds=60))
    assert (claims.workspace_id, claims.session_id, claims.user_id) == ("ws1", "s1", "alice")


def test_expired_and_tampered_rejected():
    signer = SkillTokenSigner.from_master_key(MASTER)
    token = signer.issue(workspace_id="ws1", session_id="s1", user_id="alice", ttl_seconds=60, now=1000)
    with pytest.raises(TokenError):
        signer.verify(token, now=1060)
    body, signature = token.split(".")
    with pytest.raises(TokenError):
        signer.verify(f"{body}x.{signature}", now=1000)
    for bad in ("", "nodot", "a.b", None, f"{body}.签名"):
        with pytest.raises(TokenError):
            signer.verify(bad)


def test_not_interchangeable_with_tool_tokens():
    tools = ToolTokenSigner.from_master_key(MASTER)
    tool_token = tools.issue(workspace_id="ws1", session_id="s1", user_id="u", tools=[], ttl_seconds=60)
    with pytest.raises(TokenError):
        SkillTokenSigner.from_master_key(MASTER).verify(tool_token)
    other = SkillTokenSigner.from_master_key("x" * 40)
    with pytest.raises(TokenError):
        other.verify(SkillTokenSigner.from_master_key(MASTER).issue(
            workspace_id="ws1", session_id="s1", user_id="u", ttl_seconds=60))


def test_issue_validates_input():
    signer = SkillTokenSigner.from_master_key(MASTER)
    with pytest.raises(ValueError):
        signer.issue(workspace_id="", session_id="s", user_id="u", ttl_seconds=60)
    with pytest.raises(ValueError):
        signer.issue(workspace_id="w", session_id="s", user_id="u", ttl_seconds=0)
    with pytest.raises(ValueError):
        SkillTokenSigner(b"short")
    with pytest.raises(ValueError):
        SkillTokenSigner.from_master_key("")
