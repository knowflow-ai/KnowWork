"""网关按别名路由：查模型目录取厂商地址与密钥；空目录回退固定上游；流式按字节转发；超时可配。"""

import asyncio
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


async def test_with_real_catalog(models):
    """端到端：真实模型目录 + 行级安全，网关解密密钥后转发。"""
    catalog = models.catalog
    provider = await catalog.create_provider("bob", "ws1", ProviderCreate(
        name="Qwen", base_url="http://provider/v1", api_key="sk-real-provider-0000"))
    await catalog.create_model("bob", "ws1", ModelCreate(provider_id=provider.id, alias="qwen", name="qwen-plus"))
    routed = Upstream()
    async with _client(catalog, routed=httpx.MockTransport(routed)) as client:
        ok = await client.post(URL, json={"model": "qwen", "messages": MESSAGES}, headers=_auth())
        other_ws = await client.post(URL, json={"model": "qwen", "messages": MESSAGES}, headers=_auth("ws2"))
        unknown = await client.post(URL, json={"model": "gpt", "messages": MESSAGES}, headers=_auth())
    assert ok.status_code == 200 and routed.requests[0]["auth"] == "Bearer sk-real-provider-0000"
    assert routed.requests[0]["body"]["model"] == "qwen-plus"
    assert other_ws.status_code == 200 and len(routed.requests) == 1, "别的空间看不到这个模型，走它自己的回退"
    assert unknown.status_code == 404
