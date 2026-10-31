"""内部模型网关：令牌校验、请求体校验与字段白名单、上游错误不外泄。"""

import json

import httpx
import pytest
from fastapi import FastAPI

from knowwork_server.gateway.model_gateway import build_router
from knowwork_server.kernel.tokens import TokenSigner, derive_key

pytestmark = pytest.mark.anyio

SIGNER = TokenSigner(derive_key("k" * 40, "host-callback"))
# 收集阶段签发：有效期要盖住整个测试套件的运行时间，否则排在后面跑时已过期
GOOD = {"authorization": f"Bearer {SIGNER.issue(audience='model', workspace_id='ws', ttl_seconds=3600)}"}
MESSAGES = [{"role": "user", "content": "hi"}]


class _NeverUpstream(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request):
        raise AssertionError("令牌无效或请求不合法时不应请求上游")


class Recorder:
    def __init__(self, status=200, body=b"data: {}\n\n", raise_error=None):
        self.status, self.body, self.raise_error = status, body, raise_error
        self.requests: list[dict] = []

    def __call__(self, request: httpx.Request):
        if self.raise_error is not None:
            raise self.raise_error
        self.requests.append(json.loads(request.content))
        return httpx.Response(self.status, stream=httpx.ByteStream(self.body), headers={"content-type": "text/event-stream"})


def gateway(transport):
    upstream = httpx.AsyncClient(base_url="http://upstream/v1", transport=transport)
    app = FastAPI()
    app.include_router(build_router(SIGNER, upstream, "real-model"))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://internal")


@pytest.mark.parametrize("authorization", [
    None,
    "Bearer not-a-token",
    "bearer lowercase-scheme",
    f"Bearer {SIGNER.issue(audience='tools', workspace_id='ws', ttl_seconds=60)}",
    f"Bearer {SIGNER.issue(audience='model', workspace_id='ws', ttl_seconds=60, now=0)}",
    f"Bearer {TokenSigner(derive_key('x' * 40, 'host-callback')).issue(audience='model', workspace_id='ws', ttl_seconds=60)}",
    "Bearer abc.ééé",
])
async def test_rejects_invalid_tokens(authorization):
    headers = {} if authorization is None else {"authorization": authorization.encode("latin-1")}
    async with gateway(_NeverUpstream()) as client:
        response = await client.post("/internal/model/v1/chat/completions", json={"model": "x", "messages": MESSAGES},
                                     headers=headers)
    assert response.status_code == 401


@pytest.mark.parametrize("body", [b"[]", b"\"text\"", b"{not json", b"{}", b'{"messages": []}',
                                  b'{"messages": "hi"}', b'{"messages": [{}], "stream": "yes"}',
                                  b'{"messages": [{}], "tools": {}}'])
async def test_rejects_invalid_bodies(body):
    async with gateway(_NeverUpstream()) as client:
        response = await client.post("/internal/model/v1/chat/completions", content=body,
                                     headers={**GOOD, "content-type": "application/json"})
    assert response.status_code == 400


async def test_only_whitelisted_fields_reach_upstream():
    recorder = Recorder()
    body = {"model": "default", "messages": MESSAGES, "stream": True, "stream_options": {"include_usage": True},
            "max_tokens": 10, "store": True, "prompt_cache_key": "session-abc", "provider": "x", "metadata": {"a": 1}}
    async with gateway(httpx.MockTransport(recorder)) as client:
        response = await client.post("/internal/model/v1/chat/completions", json=body, headers=GOOD)
    assert response.status_code == 200
    assert recorder.requests == [{"model": "real-model", "messages": MESSAGES, "stream": True,
                                  "stream_options": {"include_usage": True}, "max_tokens": 10}]


@pytest.mark.parametrize(("upstream_status", "expected"), [(400, 400), (401, 502), (403, 502), (404, 404),
                                                           (429, 429), (500, 502), (503, 502)])
async def test_upstream_errors_are_not_leaked(upstream_status, expected):
    secret = b'{"error": {"message": "invalid api key sk-live-SECRET for org-123"}}'
    async with gateway(httpx.MockTransport(Recorder(status=upstream_status, body=secret))) as client:
        response = await client.post("/internal/model/v1/chat/completions", json={"messages": MESSAGES},
                                     headers=GOOD)
    assert response.status_code == expected
    assert "SECRET" not in response.text and "org-123" not in response.text
    assert response.json() == {"error": {"message": "上游模型返回错误"}}


async def test_unreachable_upstream_is_502():
    recorder = Recorder(raise_error=httpx.ConnectError("connection refused to 10.0.0.5"))
    async with gateway(httpx.MockTransport(recorder)) as client:
        response = await client.post("/internal/model/v1/chat/completions", json={"messages": MESSAGES},
                                     headers=GOOD)
    assert response.status_code == 502 and "10.0.0.5" not in response.text


@pytest.mark.parametrize("body", [
    (b'{"error": {"message": "This model\'s maximum context length is 131072 tokens. However, you requested 140000 '
     b'tokens (sk-live-SECRET)", "code": "context_length_exceeded"}}'),
    b'{"error": {"message": "Range of input length should be [1, 98304]", "code": "invalid_parameter_error"}}',
])
async def test_context_overflow_is_passed_on_in_words_the_harness_recognizes(body):
    """上下文超长：回一句官方认得的说法，官方压缩才会先压缩历史再重试；厂商原文（可能带账户信息）不回传。"""
    async with gateway(httpx.MockTransport(Recorder(status=400, body=body))) as client:
        response = await client.post("/internal/model/v1/chat/completions", json={"messages": MESSAGES},
                                     headers=GOOD)
    assert response.status_code == 400 and "SECRET" not in response.text
    message = response.json()["error"]["message"]
    assert message.startswith("context_length_exceeded")
    async with gateway(httpx.MockTransport(Recorder(status=413, body=b"too big"))) as client:
        too_large = await client.post("/internal/model/v1/chat/completions", json={"messages": MESSAGES}, headers=GOOD)
    assert (too_large.status_code, too_large.json()["error"]["message"]) == (413, message)
