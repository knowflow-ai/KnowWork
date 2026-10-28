"""内部模型客户端：业务服务与 worker 自己发起的模型调用（嵌入、重排、看图、普通对话），不经宿主。

- 按别名从模型目录取路由（厂商地址与密钥），核对模型标了对应能力；不回退到别的模型；
- 都是 OpenAI 兼容接口：/embeddings、/rerank（Jina、SiliconFlow、vLLM、TEI 的写法都认）、/chat/completions；
- 后台（worker 的批量入库、富化）与交互（检索时的重排）各一份并发额度，批量入库不会把交互调用挤满；
- 不重试：后台任务由任务框架按失败类别重试，检索时重排失败由调用方退回检索结果。失败分可重试（超时、限流、5xx）
  与不可重试（鉴权、模型不存在、返回格式不对、输入超限）；message 给用户看，不带上游原文与密钥。
"""

import asyncio
import base64
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from .errors import ModelsError
from .schemas import Capability, ModelView, Route
from .service import ModelCatalog
from .thinking import with_thinking

log = logging.getLogger(__name__)

EMBED_BATCH = 32
EMBED_TIMEOUT = 60.0
RERANK_TIMEOUT = 30.0
VISION_TIMEOUT = 60.0
CHAT_TIMEOUT = 120.0


class ModelCallError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class Slots:
    """两份并发额度（每个进程各自一份）。"""

    background: int = 4
    interactive: int = 16


DEFAULT_SLOTS = Slots()


def _status_error(status: int) -> ModelCallError:
    if status in (401, 403):
        return ModelCallError("model_auth", "模型厂商鉴权失败，请检查 API 密钥", retryable=False)
    if status == 404:
        return ModelCallError("model_not_found", "模型厂商那边找不到这个模型或接口", retryable=False)
    if status == 413:
        return ModelCallError("model_input_too_large", "输入超过模型的长度上限", retryable=False)
    if status == 429:
        return ModelCallError("model_rate_limited", "模型厂商限流或额度不足", retryable=True)
    if status >= 500:
        return ModelCallError("model_unavailable", "模型服务暂时不可用", retryable=True)
    return ModelCallError("model_rejected", f"模型厂商拒绝了请求（HTTP {status}）", retryable=False)


def _malformed() -> ModelCallError:
    return ModelCallError("model_bad_response", "模型返回的结果无法识别", retryable=False)


class ModelClient:
    def __init__(self, catalog: ModelCatalog, http: httpx.AsyncClient, slots: Slots = DEFAULT_SLOTS) -> None:
        self._catalog = catalog
        self._http = http
        self._background = asyncio.Semaphore(slots.background)
        self._interactive = asyncio.Semaphore(slots.interactive)

    async def embed(self, workspace_id: str, alias: str, texts: Sequence[str], *,
                    background: bool = True) -> list[list[float]]:
        """按输入顺序返回向量；超过 32 段分批发。"""
        route = await self._route(workspace_id, alias, Capability.EMBEDDING)
        vectors: list[list[float]] = []
        for start in range(0, len(texts), EMBED_BATCH):
            batch = list(texts[start:start + EMBED_BATCH])
            body = await self._post(route, "/embeddings", {"model": route.upstream_model, "input": batch},
                                    timeout=EMBED_TIMEOUT, background=background)
            vectors.extend(_vectors(body, len(batch)))
        return vectors

    async def capable(self, workspace_id: str, capability: str) -> tuple[ModelView, ...]:
        """能拿来做这件事的模型（启用的、带这种能力的），扩展的设置页选模型用；调用时不看个人授权。"""
        return await self._catalog.capable_models(workspace_id, capability)

    async def input_limit(self, workspace_id: str, alias: str) -> int | None:
        """嵌入模型的最大输入长度（token），管理员没填时为 None。超出的部分上游会悄悄截掉，
        调用方按它定每段输入的长短。"""
        return (await self._route(workspace_id, alias, Capability.EMBEDDING)).context_length

    async def rerank(self, workspace_id: str, alias: str, query: str, documents: Sequence[str], *,
                     background: bool = False) -> list[float]:
        """按文档顺序返回重排分。"""
        if not documents:
            return []
        route = await self._route(workspace_id, alias, Capability.RERANK)
        body = await self._post(route, "/rerank", {"model": route.upstream_model, "query": query,
                                                   "documents": list(documents), "return_documents": False},
                                timeout=RERANK_TIMEOUT, background=background)
        return _scores(body, len(documents))

    async def chat(self, workspace_id: str, alias: str, messages: Sequence[dict[str, Any]], *,
                   max_tokens: int = 1024, temperature: float = 0.2, background: bool = True,
                   capability: str = Capability.CHAT, response_format: dict[str, Any] | None = None) -> str:
        """response_format 原样交给厂商（要结构化输出的调用方用）；不传就不带。"""
        route = await self._route(workspace_id, alias, capability)
        request = with_thinking({"model": route.upstream_model, "messages": list(messages), "max_tokens": max_tokens,
                                 "temperature": temperature, "stream": False, **(
                                     {"response_format": response_format} if response_format else {})},
                                route.thinking, route.base_url)
        body = await self._post(route, "/chat/completions", request,
                                timeout=VISION_TIMEOUT if capability == Capability.VISION else CHAT_TIMEOUT,
                                background=background)
        return _content(body)

    async def describe_image(self, workspace_id: str, alias: str, image: bytes, mime: str, prompt: str, *,
                             max_tokens: int = 512, background: bool = True) -> str:
        url = f"data:{mime};base64,{base64.b64encode(image).decode('ascii')}"
        messages = [{"role": "user", "content": [{"type": "text", "text": prompt},
                                                 {"type": "image_url", "image_url": {"url": url}}]}]
        return await self.chat(workspace_id, alias, messages, max_tokens=max_tokens, background=background,
                               capability=Capability.VISION)

    async def _route(self, workspace_id: str, alias: str, capability: str) -> Route:
        try:
            return await self._catalog.resolve_capable(workspace_id, alias, capability)
        except ModelsError as error:
            raise ModelCallError("model_not_configured", str(error), retryable=False) from error

    async def _post(self, route: Route, path: str, body: dict, *, timeout: float, background: bool) -> Any:
        headers = {"authorization": f"Bearer {route.api_key}"} if route.api_key else {}
        async with self._background if background else self._interactive:
            try:
                response = await self._http.post(route.base_url + path, json=body, headers=headers, timeout=timeout)
            except httpx.TimeoutException as error:
                raise ModelCallError("model_timeout", "模型调用超时", retryable=True) from error
            except httpx.HTTPError as error:
                raise ModelCallError("model_unavailable", "连不上模型服务", retryable=True) from error
        if response.status_code >= 400:
            log.warning("model %s %s returned HTTP %s", route.alias, path, response.status_code)
            raise _status_error(response.status_code)
        try:
            return response.json()
        except ValueError as error:
            raise _malformed() from error


def _vectors(body: Any, count: int) -> list[list[float]]:
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list) or len(data) != count:
        raise _malformed()
    try:
        ordered = sorted(data, key=lambda item: item["index"])
        vectors = [[float(x) for x in item["embedding"]] for item in ordered]
    except (KeyError, TypeError, ValueError) as error:
        raise _malformed() from error
    if [item["index"] for item in ordered] != list(range(count)):
        raise _malformed()
    return vectors


def _scores(body: Any, count: int) -> list[float]:
    """{"results": [{"index", "relevance_score"}]}（Jina、SiliconFlow、vLLM）或 [{"index", "score"}]（TEI）。"""
    items = body.get("results") if isinstance(body, dict) else body
    if not isinstance(items, list):
        raise _malformed()
    scores = [0.0] * count
    seen = set()
    try:
        for item in items:
            index = int(item["index"])
            score = item.get("relevance_score", item.get("score"))
            if not 0 <= index < count or score is None:
                raise _malformed()
            scores[index] = float(score)
            seen.add(index)
    except (KeyError, TypeError, ValueError) as error:
        raise _malformed() from error
    if len(seen) != count:  # 有的厂商只回前 top_n 条；我们没传 top_n，少了就是不对
        raise _malformed()
    return scores


def _content(body: Any) -> str:
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise _malformed() from error
    if not isinstance(content, str):
        raise _malformed()
    return content
