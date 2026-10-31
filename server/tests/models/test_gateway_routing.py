"""网关按别名路由：查模型目录取厂商地址与密钥；空目录回退固定上游；流式按字节转发；超时可配。"""

import asyncio
import gzip
import json

import httpx
import pytest
from fastapi import FastAPI

from knowwork_server.gateway.model_gateway import GatewayLimits, build_router
from knowwork_server.kernel.tokens import TokenSigner, derive_key
from knowwork_server.models.errors import NotFound, Unavailable
from knowwork_server.models.schemas import ModelCreate, ProviderCreate, Route

pytestmark = pytest.mark.anyio
SIGNER = TokenSigner(derive_key("k" * 40, "host-callback"))
URL = "/internal/model/v1/chat/completions"
MESSAGES = [{"role": "user", "content": "hi"}]
FIXED_KEY = "sk-fixed-upstream-key"


def _auth(workspace_id="ws1"):
    return {"authorization": f"Bearer {SIGNER.issue(audience='model', workspace_id=workspace_id, ttl_seconds=60)}"}


class Upstream:
    def __init__(self, chunks=(b"data: {\"a\":1}\n\n", b"data: [DONE]\n\n"), status=200):
        self.chunks, self.status, self.requests = chunks, status, []

    def __call__(self, request: httpx.Request):
        self.requests.append({"url": str(request.url), "auth": request.headers.get("authorization"),
                              "body": json.loads(request.content)})
        return httpx.Response(self.status, stream=httpx.ByteStream(b"".join(self.chunks)),
                              headers={"content-type": "text/event-stream"})


class Resolver:
    def __init__(self, result):
        self.result, self.calls = result, []

    async def resolve(self, workspace_id, alias):
        self.calls.append((workspace_id, alias))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


ROUTE = Route(model_id="md_1", alias="qwen", upstream_model="qwen-max-latest", base_url="http://provider/v1",
              api_key="sk-provider-key")


def _client(resolver, *, fixed=None, routed=None, upstream_model="fixed-model", limits=None):
    fixed = fixed or Upstream()
    upstream = httpx.AsyncClient(base_url="http://fixed/v1", headers={"authorization": f"Bearer {FIXED_KEY}"},
                                 transport=httpx.MockTransport(fixed))
    http = httpx.AsyncClient(transport=routed) if routed is not None else None
    app = FastAPI()
    app.include_router(build_router(SIGNER, upstream, upstream_model, resolver=resolver, http=http, limits=limits))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://internal")


async def test_routes_by_alias_with_provider_key():
    routed, fixed = Upstream(), Upstream()
    resolver = Resolver(ROUTE)
    async with _client(resolver, fixed=fixed, routed=httpx.MockTransport(routed)) as client:
        response = await client.post(URL, json={"model": "qwen", "messages": MESSAGES, "store": True}, headers=_auth())
    assert response.status_code == 200
    assert resolver.calls == [("ws1", "qwen")], "工作空间取自令牌"
    sent = routed.requests[0]
    assert sent["url"] == "http://provider/v1/chat/completions"
    assert sent["auth"] == "Bearer sk-provider-key", "必须带对应厂商的密钥，不能带固定上游的"
    assert sent["body"] == {"model": "qwen-max-latest", "messages": MESSAGES}, "白名单过滤照旧生效"
    assert fixed.requests == []


async def test_the_models_thinking_setting_is_added_to_the_request():
    """「管理 → 模型」里给对话模型关了思考：转发时按服务商的写法补上（自建的 vLLM、SGLang 用 chat_template_kwargs）。"""
    routed = Upstream()
    quiet = Route(model_id="m", alias="qwen", upstream_model="qwen3-32b", base_url="http://10.0.0.6:8000/v1",
                  api_key="", thinking="off")
    async with _client(Resolver(quiet), routed=httpx.MockTransport(routed)) as client:
        await client.post(URL, json={"model": "qwen", "messages": MESSAGES}, headers=_auth())
    assert routed.requests[0]["body"] == {"model": "qwen3-32b", "messages": MESSAGES,
                                          "chat_template_kwargs": {"enable_thinking": False}}


async def test_max_tokens_is_capped_at_the_models_output_limit():
    """宿主按全局默认请求 max_tokens，模型上限更小时厂商会直接拒：填了「最大输出」就收紧到它，没填原样转发。"""
    routed = Upstream()
    small = Route(model_id="m", alias="small", upstream_model="deepseek-chat", base_url="http://provider/v1",
                  api_key="", max_output_tokens=8192)
    async with _client(Resolver(small), routed=httpx.MockTransport(routed)) as client:
        await client.post(URL, json={"model": "small", "messages": MESSAGES, "max_tokens": 32768}, headers=_auth())
        await client.post(URL, json={"model": "small", "messages": MESSAGES, "max_tokens": 1000}, headers=_auth())
        await client.post(URL, json={"model": "small", "messages": MESSAGES}, headers=_auth())
    assert [r["body"].get("max_tokens") for r in routed.requests] == [8192, 1000, None]
    unlimited = Upstream()
    free = Route(model_id="m", alias="free", upstream_model="x", base_url="http://provider/v1", api_key="")
    async with _client(Resolver(free), routed=httpx.MockTransport(unlimited)) as client:
        await client.post(URL, json={"model": "free", "messages": MESSAGES, "max_tokens": 32768}, headers=_auth())
    assert unlimited.requests[0]["body"]["max_tokens"] == 32768


async def test_route_without_key_sends_no_authorization():
    routed = Upstream()
    keyless = Route(model_id="m", alias="local", upstream_model="llama", base_url="http://vllm/v1", api_key="")
    async with _client(Resolver(keyless), routed=httpx.MockTransport(routed)) as client:
        assert (await client.post(URL, json={"model": "local", "messages": MESSAGES}, headers=_auth())).status_code == 200
    assert routed.requests[0]["auth"] is None


async def test_streams_bytes_through():
    chunks = tuple(f"data: {{\"i\":{i}}}\n\n".encode() for i in range(5)) + (b"data: [DONE]\n\n",)
    body = {"model": "qwen", "messages": MESSAGES, "stream": True}
    async with (_client(Resolver(ROUTE), routed=httpx.MockTransport(Upstream(chunks))) as client,
                client.stream("POST", URL, json=body, headers=_auth()) as response):
        received = b"".join([chunk async for chunk in response.aiter_raw()])
        assert response.headers["content-type"].startswith("text/event-stream")
    assert received == b"".join(chunks)


class GzipUpstream(Upstream):
    """不管请求头怎么写都把 SSE 压成 gzip 的上游（DeepSeek 官方接口就会压）。记下请求带的 accept-encoding。"""

    def __call__(self, request: httpx.Request):
        self.requests.append({"accept_encoding": request.headers.get("accept-encoding")})
        return httpx.Response(self.status, stream=httpx.ByteStream(gzip.compress(b"".join(self.chunks))),
                              headers={"content-type": "text/event-stream", "content-encoding": "gzip"})


@pytest.mark.parametrize("catalog", [True, False], ids=["routed", "fixed"])
async def test_compressed_upstream_streams_are_decoded_before_relaying(catalog):
    """上游把流压缩了：宿主拿到的必须是解压后的 SSE，也不能带 content-encoding（否则宿主解不出 data 行，
    每轮都报「Stream ended without finish_reason」）。请求时就说不要压缩，流式出字不被压缩攒块拖慢。"""
    chunks = (b"data: {\"choices\":[{\"delta\":{\"content\":\"hi\"}}]}\n\n", b"data: [DONE]\n\n")
    upstream = GzipUpstream(chunks)
    resolver = Resolver(ROUTE if catalog else None)
    transport = httpx.MockTransport(upstream)
    kwargs = {"routed": transport} if catalog else {"routed": httpx.MockTransport(Upstream()), "fixed": upstream}
    async with (_client(resolver, **kwargs) as client,
                client.stream("POST", URL, json={"model": "qwen", "messages": MESSAGES, "stream": True},
                              headers=_auth()) as response):
        received = b"".join([chunk async for chunk in response.aiter_raw()])
        assert "content-encoding" not in response.headers
    assert received == b"".join(chunks)
    assert upstream.requests == [{"accept_encoding": "identity"}]


async def test_empty_catalog_falls_back_to_fixed_upstream():
    fixed = Upstream()
    async with _client(Resolver(None), fixed=fixed, routed=httpx.MockTransport(Upstream())) as client:
        response = await client.post(URL, json={"model": "default", "messages": MESSAGES}, headers=_auth())
    assert response.status_code == 200
    assert fixed.requests[0]["auth"] == f"Bearer {FIXED_KEY}" and fixed.requests[0]["body"]["model"] == "fixed-model"


async def test_empty_catalog_without_fallback_is_404():
    async with _client(Resolver(None), routed=httpx.MockTransport(Upstream()), upstream_model="") as client:
        assert (await client.post(URL, json={"model": "x", "messages": MESSAGES}, headers=_auth())).status_code == 404


@pytest.mark.parametrize(("error", "status"), [(NotFound(), 404), (Unavailable(), 503)])
async def test_unknown_or_unavailable_model_does_not_fall_back(error, status):
    fixed = Upstream()
    async with _client(Resolver(error), fixed=fixed, routed=httpx.MockTransport(Upstream())) as client:
        response = await client.post(URL, json={"model": "nope", "messages": MESSAGES}, headers=_auth())
    assert response.status_code == status and fixed.requests == []


async def test_missing_model_name_is_400():
    async with _client(Resolver(ROUTE), routed=httpx.MockTransport(Upstream())) as client:
        assert (await client.post(URL, json={"messages": MESSAGES}, headers=_auth())).status_code == 400


async def test_routed_upstream_errors_are_not_leaked():
    secret = (b'{"error":"bad key sk-provider-key"}',)
    async with _client(Resolver(ROUTE), routed=httpx.MockTransport(Upstream(secret, status=401))) as client:
        response = await client.post(URL, json={"model": "qwen", "messages": MESSAGES}, headers=_auth())
    assert response.status_code == 502 and "sk-provider" not in response.text


def test_catalog_requires_separate_http_client():
    upstream = httpx.AsyncClient(base_url="http://fixed/v1")
    with pytest.raises(ValueError):
        build_router(SIGNER, upstream, "m", resolver=Resolver(ROUTE))
    with pytest.raises(ValueError):
        build_router(SIGNER, upstream, "m", resolver=Resolver(ROUTE), http=upstream)


class _Slow(httpx.AsyncBaseTransport):
    def __init__(self, first_delay=0.0, gap=0.0):
        self.first_delay, self.gap = first_delay, gap

    async def handle_async_request(self, request):
        await asyncio.sleep(self.first_delay)
        gap = self.gap

        class Body(httpx.AsyncByteStream):
            async def __aiter__(self):
                for i in range(50):
                    yield f"data: {i}\n\n".encode()
                    await asyncio.sleep(gap)

        return httpx.Response(200, stream=Body(), headers={"content-type": "text/event-stream"})


async def test_total_timeout_before_response_is_504():
    limits = GatewayLimits(total_timeout_seconds=0.05)
    async with _client(Resolver(ROUTE), routed=_Slow(first_delay=1.0), limits=limits) as client:
        response = await client.post(URL, json={"model": "qwen", "messages": MESSAGES}, headers=_auth())
    assert response.status_code == 504 and response.json() == {"error": {"message": "上游模型响应超时"}}


async def test_total_timeout_truncates_long_stream():
    limits = GatewayLimits(total_timeout_seconds=0.15)
    async with _client(Resolver(ROUTE), routed=_Slow(gap=0.02), limits=limits) as client:
        response = await client.post(URL, json={"model": "qwen", "messages": MESSAGES}, headers=_auth())
    assert response.status_code == 200
    assert 0 < response.text.count("data:") < 50, "超过总时长后截断"


def _delta(i):
    return f'data: {{"choices":[{{"delta":{{"content":"第{i}段"}}}}]}}\n\n'.encode()


class _Prefill(httpx.AsyncBaseTransport):
    """先回响应头，正文第一块要等「预填充」（长上下文的自建大模型可能要一两分钟），之后可能在某处卡住。
    role_first：有的模型服务一收到请求就先发一个只有 role、没有内容的块，再去预填充。"""

    def __init__(self, first_chunk_delay=0.0, stall_after=None, stall=0.0, role_first=False):
        self.first_chunk_delay, self.stall_after, self.stall = first_chunk_delay, stall_after, stall
        self.role_first = role_first

    async def handle_async_request(self, request):
        first, stall_after, stall, role_first = self.first_chunk_delay, self.stall_after, self.stall, self.role_first

        class Body(httpx.AsyncByteStream):
            async def __aiter__(self):
                if role_first:
                    yield b'data: {"choices":[{"delta":{"role":"assistant","content":""}}]}\n\n'
                await asyncio.sleep(first)
                for i in range(20):
                    if i == stall_after:
                        await asyncio.sleep(stall)
                    yield _delta(i)
                    await asyncio.sleep(0.005)

        return httpx.Response(200, stream=Body(), headers={"content-type": "text/event-stream"})


async def test_slow_first_token_waits_within_total_timeout():
    """首块之前只受总时长约束：等预填充比空闲超时久也不掐断（实测 3 万 token 上下文的首字超过 60 秒）。"""
    limits = GatewayLimits(total_timeout_seconds=2.0, idle_timeout_seconds=0.1)
    async with _client(Resolver(ROUTE), routed=_Prefill(first_chunk_delay=0.4), limits=limits) as client:
        response = await client.post(URL, json={"model": "qwen", "messages": MESSAGES}, headers=_auth())
    assert response.status_code == 200 and response.text.count("第") == 20


async def test_empty_role_chunk_before_prefill_does_not_start_idle_timer():
    """先发空的 role 块再预填充：空块不算开始出字，首个有内容的块之前仍只受总时长约束。"""
    limits = GatewayLimits(total_timeout_seconds=2.0, idle_timeout_seconds=0.1)
    transport = _Prefill(first_chunk_delay=0.4, role_first=True)
    async with _client(Resolver(ROUTE), routed=transport, limits=limits) as client:
        response = await client.post(URL, json={"model": "qwen", "messages": MESSAGES}, headers=_auth())
    assert response.status_code == 200 and response.text.count("第") == 20


async def test_stall_after_streaming_starts_is_cut_at_idle_timeout():
    limits = GatewayLimits(total_timeout_seconds=5.0, idle_timeout_seconds=0.1)
    started = asyncio.get_running_loop().time()
    async with _client(Resolver(ROUTE), routed=_Prefill(stall_after=5, stall=1.5), limits=limits) as client:
        response = await client.post(URL, json={"model": "qwen", "messages": MESSAGES}, headers=_auth())
    assert response.status_code == 200 and response.text.count("第") == 5, "开始出字后卡住超过空闲超时就截断"
    assert asyncio.get_running_loop().time() - started < 1.2, "按空闲超时截断，不等到卡住结束"


class _ToolCall(httpx.AsyncBaseTransport):
    """先出几段思考，再开始一个工具调用：只发出文件路径，正文参数攒了 stall 秒才一次发完。
    null_tool_calls：有的模型服务在普通正文块里也带 "tool_calls": null，这不算开始工具调用。"""

    def __init__(self, stall: float, null_tool_calls=False):
        self.stall, self.null_tool_calls = stall, null_tool_calls

    async def handle_async_request(self, request):
        stall, null_tool_calls = self.stall, self.null_tool_calls

        class Body(httpx.AsyncByteStream):
            async def __aiter__(self):
                extra = ',"tool_calls":null' if null_tool_calls else ''
                for i in range(3):
                    yield f'data: {{"choices":[{{"delta":{{"reasoning_content":"想{i}"{extra}}}}}]}}\n\n'.encode()
                if null_tool_calls:
                    await asyncio.sleep(stall)
                    yield b'data: {"choices":[{"delta":{"content":"tail"}}]}\n\n'
                    return
                call = '{"index":0,"id":"call_1","type":"function","function":{"name":"write","arguments":"{\\"file_path\\": \\"/w/a.js\\""}}'
                yield f'data: {{"choices":[{{"delta":{{"tool_calls":[{call}]}}}}]}}\n\n'.encode()
                await asyncio.sleep(stall)
                rest = '{"index":0,"function":{"arguments":", \\"content\\": \\"脚本正文\\"}"}}'
                yield f'data: {{"choices":[{{"delta":{{"tool_calls":[{rest}]}}}}]}}\n\n'.encode()
                yield b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\ndata: [DONE]\n\n'

        return httpx.Response(200, stream=Body(), headers={"content-type": "text/event-stream"})


async def test_buffered_tool_call_arguments_wait_within_total_timeout():
    """工具调用开始后只受总时长约束：自建推理服务常把一个参数整段攒完再发，写长脚本时一两分钟没有一个字节
    （实测发出 file_path 后静默超过 60 秒被截断，三次重试都一样，整轮失败）。"""
    limits = GatewayLimits(total_timeout_seconds=2.0, idle_timeout_seconds=0.1)
    async with _client(Resolver(ROUTE), routed=_ToolCall(stall=0.4), limits=limits) as client:
        response = await client.post(URL, json={"model": "qwen", "messages": MESSAGES}, headers=_auth())
    assert response.status_code == 200
    assert "脚本正文" in response.text and '"finish_reason":"tool_calls"' in response.text


async def test_null_tool_calls_field_does_not_lift_idle_timeout():
    """正文块里带 "tool_calls": null 不算开始工具调用，卡住照样按空闲超时截断。"""
    limits = GatewayLimits(total_timeout_seconds=5.0, idle_timeout_seconds=0.1)
    started = asyncio.get_running_loop().time()
    async with _client(Resolver(ROUTE), routed=_ToolCall(stall=1.5, null_tool_calls=True), limits=limits) as client:
        response = await client.post(URL, json={"model": "qwen", "messages": MESSAGES}, headers=_auth())
    assert response.status_code == 200 and "tail" not in response.text
    assert asyncio.get_running_loop().time() - started < 1.2


def test_httpx_read_timeout_does_not_cut_the_first_token():
    """httpx 的读超时只兜底总时长：空闲超时由转发循环自己管，免得它把首字之前的等待也算进去。"""
    timeout = GatewayLimits(total_timeout_seconds=300.0, idle_timeout_seconds=60.0).httpx_timeout()
    assert timeout.read == 300.0 and timeout.connect == 10.0


async def test_with_real_catalog(models):
    """端到端：真实模型目录 + 行级安全，网关解密密钥后转发。"""
    catalog = models.catalog
    provider = await catalog.create_provider("root", ProviderCreate(
        name="Qwen", base_url="http://provider/v1", api_key="sk-real-provider-0000"))
    await catalog.create_model("root", ModelCreate(provider_id=provider.id, alias="qwen", name="qwen-plus"))
    routed = Upstream()
    async with _client(catalog, routed=httpx.MockTransport(routed)) as client:
        ok = await client.post(URL, json={"model": "qwen", "messages": MESSAGES}, headers=_auth())
        other_ws = await client.post(URL, json={"model": "qwen", "messages": MESSAGES}, headers=_auth("ws2"))
        unknown = await client.post(URL, json={"model": "gpt", "messages": MESSAGES}, headers=_auth())
    assert ok.status_code == 200 and routed.requests[0]["auth"] == "Bearer sk-real-provider-0000"
    assert routed.requests[0]["body"]["model"] == "qwen-plus"
    assert other_ws.status_code == 200 and len(routed.requests) == 2, "模型全平台共用，别的空间也用这一个"
    assert unknown.status_code == 404
