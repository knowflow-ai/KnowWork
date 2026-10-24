"""对外检索结果里的短期签名链接：站内相对路径才签；签名覆盖路径、用户、过期时间；过期、改动都不认。"""

from urllib.parse import parse_qs, urlsplit

from knowwork_server.knowledge.links import EXPIRY_STEP, TTL_SECONDS, LinkSigner

PATH = "/api/workspaces/ws1/library/bases/kb1/images/" + "a" * 64


def params(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def test_signed_links_verify_for_the_user_until_they_expire():
    now = [1_000_000.0]
    signer = LinkSigner(b"k" * 32, clock=lambda: now[0])
    url = signer.sign(PATH, "alice")
    expires = int(params(url)["exp"])
    assert urlsplit(url).path == PATH and 1_000_000 + TTL_SECONDS <= expires < 1_000_000 + TTL_SECONDS + EXPIRY_STEP
    assert signer.verify(PATH, params(url)) == "alice"
    now[0] = expires + 1
    assert signer.verify(PATH, params(url)) is None, "过期不认"


def test_signing_again_soon_gives_the_same_link():
    """对话里每次检索返回都重新拉出处：链接不变，回答里的图才不会重新加载、闪一下。"""
    now = [1_000_100.0]  # 离取整边界还有 100 秒
    signer = LinkSigner(b"k" * 32, clock=lambda: now[0])
    first = signer.sign(PATH, "alice")
    now[0] += 7
    assert signer.sign(PATH, "alice") == first
    assert signer.sign(PATH, "bob") != first, "换了人仍是不同的链接"


def test_tampered_or_foreign_links_are_rejected():
    signer = LinkSigner(b"k" * 32)
    good = params(signer.sign(PATH, "alice"))
    assert signer.verify(PATH.replace("kb1", "kb2"), good) is None, "换了路径不认"
    assert signer.verify(PATH, {**good, "uid": "bob"}) is None, "换了用户不认"
    assert signer.verify(PATH, {**good, "sig": "0" * 64}) is None
    assert LinkSigner(b"x" * 32).verify(PATH, good) is None, "换了密钥不认"
    assert signer.verify(PATH, {}) is None
    for url in ("https://example.com/a.png", "/other/a.png", f"{PATH}?size=1"):
        assert signer.sign(url, "alice") == url, "不是站内无参数的接口路径就原样返回"
