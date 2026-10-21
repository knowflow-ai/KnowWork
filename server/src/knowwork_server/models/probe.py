"""连通测试：按模型能力向厂商发一次最小请求，只回结论，不回上游原文（可能夹带密钥或内部地址）。

- 对话 / 视觉：POST /chat/completions，max_tokens=1；
- 向量：POST /embeddings；
- 重排：POST /rerank（Jina、SiliconFlow、vLLM 的通行写法）。
"""

import time
from dataclasses import dataclass

import httpx

from .schemas import Capability, Route

DEFAULT_PROBE_TIMEOUT = 15.0
_PING = "ping"


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    capability: str
    latency_ms: int
    upstream_status: int | None
    message: str


def _request_for(capability: str, model: str) -> tuple[str, dict]:
    if capability == Capability.EMBEDDING:
        return "/embeddings", {"model": model, "input": _PING}
    if capability == Capability.RERANK:
        return "/rerank", {"model": model, "query": _PING, "documents": [_PING]}
    return "/chat/completions", {"model": model, "messages": [{"role": "user", "content": _PING}],
                                 "max_tokens": 1, "stream": False}


def _message_for(status: int) -> str:
    if status in (401, 403):
        return "鉴权失败，请检查 API 密钥"
    if status == 404:
        return "地址或模型名不存在"
    if status == 429:
        return "上游限流或额度不足"
    return f"上游返回错误（HTTP {status}）"


def pick_capability(capabilities: tuple[str, ...]) -> str:
    for preferred in (Capability.CHAT, Capability.VISION, Capability.EMBEDDING, Capability.RERANK):
        if preferred in capabilities:
            return preferred.value
    return Capability.CHAT.value


async def probe(http: httpx.AsyncClient, route: Route, capability: str, *,
                timeout: float = DEFAULT_PROBE_TIMEOUT) -> ProbeResult:
    path, body = _request_for(capability, route.upstream_model)
    headers = {"authorization": f"Bearer {route.api_key}"} if route.api_key else {}
    started = time.monotonic()

    def done(ok: bool, status: int | None, message: str) -> ProbeResult:
        return ProbeResult(ok=ok, capability=capability, latency_ms=int((time.monotonic() - started) * 1000),
                           upstream_status=status, message=message)

    try:
        response = await http.post(route.base_url + path, json=body, headers=headers, timeout=timeout)
    except httpx.TimeoutException:
        return done(False, None, "连接超时")
    except httpx.HTTPError:
        return done(False, None, "无法连接到 API 地址")
    if response.status_code >= 400:
        return done(False, response.status_code, _message_for(response.status_code))
    try:
        response.json()
    except ValueError:
        return done(False, response.status_code, "上游返回的不是 JSON，地址可能不是 OpenAI 兼容接口")
    return done(True, response.status_code, "连通正常")
