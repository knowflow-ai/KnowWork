"""网关内容检查的纯逻辑：从请求里取哪些文字、流式输出怎么分段查（跨两次检查的词也查得到）；专属宿主的模型令牌带归属，
轮换令牌时每台专属宿主各拿自己的（连同按人签的激活令牌）。"""

import json

import pytest

from knowwork_server.gateway.content_check import CheckUnavailable, Verdict
from knowwork_server.gateway.content_guard import guard_stream, request_segments
from knowwork_server.hosts.launcher import HostKey
from knowwork_server.hosts.routed import RoutedKernel
from knowwork_server.kernel.tokens import TokenOwner, TokenSigner, derive_key

pytestmark = pytest.mark.anyio
WORD = "星河计划"


def test_request_segments_split_what_the_model_is_given_from_what_it_wrote():
    body = {"messages": [
        {"role": "system", "content": "系统提示"},
        {"role": "user", "content": [{"type": "text", "text": "看这张图"},
                                     {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]},
        {"role": "assistant", "content": "我先查一下", "reasoning_content": "想想",
         "tool_calls": [{"id": "c", "type": "function",
                         "function": {"name": "send", "arguments": json.dumps({"to": WORD})}}]},
        {"role": "tool", "tool_call_id": "c", "content": "已发送"},
    ], "tools": [{"type": "function", "function": {"name": "send", "description": "发消息"}}]}
    given, written = request_segments(body)
    assert given[:3] == ["系统提示", "看这张图", "已发送"], "图片是 data: 地址，不查"
    assert "发消息" in given[3] and "send" in given[3], "工具定义也查"
    assert written == [f"我先查一下\n想想\nsend\nto\n{WORD}"], "参数里转义的中文解开以后再查"


def sse(*texts: str) -> list[bytes]:
    return [b"data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": text}}]}).encode() + b"\n\n"
            for text in texts]


async def run(chunks: list[bytes], scan) -> bytes:
    async def source():
        for chunk in chunks:
            yield chunk

    ticks = iter(range(10_000))
    return b"".join([part async for part in guard_stream(source(), scan, 3, clock=lambda: float(next(ticks)))])


async def test_each_check_reaches_back_holdback_characters_so_split_words_are_found():
    seen = []

    async def scan(direction, segments, tool):
        seen.append(segments)
        return Verdict(blocked=any(WORD in segment for segment in segments), message="拦")

    out = await run(sse("一二三星", "河计划请查收"), scan)  # 时钟每读一次走一秒：每个事件都查
    assert seen == [["一二三星"], ["二三星河计划请查收"]], "第二次往回带了 3 个字"
    assert WORD not in out.decode() and out.decode().endswith(
        'data: {"error": {"message": "knowwork-content-blocked: 拦", "type": "content_blocked"}}\n\n')


async def test_events_split_across_byte_chunks_and_crlf_are_reassembled():
    async def scan(direction, segments, tool):
        return Verdict(blocked=False)

    whole = b"".join(sse("你好", "世界")) + b"data: [DONE]\n\n"
    out = await run([whole[:7], whole[7:30].replace(b"\n", b"\r\n"), whole[30:]], scan)
    assert out.count(b"data: ") == 3 and b"[DONE]" in out


async def test_an_unreachable_check_ends_the_stream_instead_of_letting_it_through():
    async def scan(direction, segments, tool):
        raise CheckUnavailable("down")

    out = (await run(sse("你好"), scan)).decode()
    assert "你好" not in out and "knowwork-check-unavailable: " in out


def test_tokens_carry_an_optional_owner():
    signer = TokenSigner(derive_key("t" * 40, "host-callback"))
    plain = signer.verify(signer.issue(audience="model", workspace_id="platform", ttl_seconds=60), audience="model")
    owned = signer.verify(signer.issue(audience="model", workspace_id="platform", ttl_seconds=60,
                                       owner=TokenOwner("ws1", "alice")), audience="model")
    assert plain.owner is None
    assert owned.owner == TokenOwner("ws1", "alice") and owned.workspace_id == "platform", "模型照旧按平台查"


class FakeKernel:
    def __init__(self) -> None:
        self.tokens = []

    async def rotate_model_credential(self, token: str) -> None:
        self.tokens.append(token)

    async def rotate_activation_credential(self, token: str) -> None:
        self.tokens.append(token)


class FakeHosts:
    def __init__(self, items) -> None:
        self.items = items

    async def kernels(self):
        return self.items


async def test_rotation_gives_each_dedicated_host_its_own_owner_token():
    shared, alice, bob = FakeKernel(), FakeKernel(), FakeKernel()
    hosts = FakeHosts(((HostKey("ws", "alice"), alice), (HostKey("ws", "bob"), bob)))
    routed = RoutedKernel(shared, hosts, placement=None, plugins_of=None,
                          owner_token=lambda key: f"owner:{key.user_id}",
                          activation_token=lambda key: f"activation:{key.user_id}")
    await routed.rotate_model_credential("platform")
    assert shared.tokens == ["platform"], "共享宿主只拿平台的模型令牌，激活照旧用宿主令牌"
    assert alice.tokens == ["owner:alice", "activation:alice"] and bob.tokens == ["owner:bob", "activation:bob"], \
        "专属宿主不拿平台令牌；按人签的激活令牌一起续"
