"""网页嵌入的会话令牌：emb_ 开头、签名对不上或过期都不认；载荷带空间、渠道、落到的用户、访客与发布令牌指纹；
别的用途的令牌（工具桥）拿来也不认；访客 id 格式不对不签。"""

import pytest

from knowwork_server.channels.web_tokens import TTL_SECONDS, EmbedTokens, key_print
from knowwork_server.kernel.tokens import TokenError
from knowwork_server.tools.tokens import ToolTokenSigner

MASTER = "test-master-key-for-web-embed-0123456789"
VISITOR = "v-0123456789abcdef"


def issue(tokens: EmbedTokens, **overrides) -> str:
    values = {"workspace_id": "ws1", "channel_id": "ch_1", "user_id": "ch_1", "visitor": VISITOR,
              "key_print": key_print("ws1~abc"), "now": 1_000}
    return tokens.issue(**{**values, **overrides})


def test_a_token_round_trips_with_what_it_was_issued_for():
    tokens = EmbedTokens.from_master_key(MASTER)
    token = issue(tokens)
    assert token.startswith("emb_")
    claims = tokens.verify(token, now=1_000 + TTL_SECONDS - 1)
    assert (claims.workspace_id, claims.channel_id, claims.user_id, claims.visitor) == ("ws1", "ch_1", "ch_1", VISITOR)
    assert claims.key_print == key_print("ws1~abc")
    assert claims.expires_at == 1_000 + TTL_SECONDS


def test_identified_tokens_carry_no_visitor():
    tokens = EmbedTokens.from_master_key(MASTER)
    claims = tokens.verify(issue(tokens, user_id="u_zhang", visitor=None), now=1_000)
    assert (claims.user_id, claims.visitor) == ("u_zhang", None)


@pytest.mark.parametrize("mangle", [
    lambda t: t[:-2] + ("A" if t[-1] != "A" else "B") + t[-1],  # 改了签名
    lambda t: t.removeprefix("emb_"),  # 没有前缀
    lambda t: "emb_" + t[4:].split(".")[0],  # 没有签名
    lambda t: "emb_garbage.garbage",
])
def test_tampered_tokens_are_refused(mangle):
    tokens = EmbedTokens.from_master_key(MASTER)
    with pytest.raises(TokenError):
        tokens.verify(mangle(issue(tokens)), now=1_000)


def test_expired_tokens_are_refused():
    tokens = EmbedTokens.from_master_key(MASTER)
    with pytest.raises(TokenError):
        tokens.verify(issue(tokens), now=1_000 + TTL_SECONDS)


def test_tokens_of_another_deployment_or_purpose_are_refused():
    mine = EmbedTokens.from_master_key(MASTER)
    with pytest.raises(TokenError):
        mine.verify(issue(EmbedTokens.from_master_key("another-master-key-0123456789abcdef")), now=1_000)
    # 工具桥令牌换上 emb_ 前缀也不行：子密钥按用途派生
    tool_token = ToolTokenSigner.from_master_key(MASTER).issue(
        workspace_id="ws1", session_id="s", user_id="u", tools=[], ttl_seconds=60, now=1_000)
    with pytest.raises(TokenError):
        mine.verify("emb_" + tool_token, now=1_000)


@pytest.mark.parametrize("visitor", ["short", "has space in it 0123", "x" * 65, "中文访客编号0123456789"])
def test_malformed_visitor_ids_are_not_signed(visitor):
    with pytest.raises(ValueError):
        issue(EmbedTokens.from_master_key(MASTER), visitor=visitor)


def test_the_key_print_changes_when_the_publish_key_is_reset():
    assert key_print("ws1~abc") != key_print("ws1~abd")
    assert len(key_print("ws1~abc")) == 16
