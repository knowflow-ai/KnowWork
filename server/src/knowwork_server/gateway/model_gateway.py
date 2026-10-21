"""内部模型网关：宿主经官方 dsh-llm-pi-ai 以 OpenAI 兼容协议调用这里。

只挂在内部端口。校验宿主携带的短期令牌（受众 model）后，换成真正的厂商地址、密钥与模型名，
把流式响应原样转回。厂商密钥只在业务服务里，宿主拿不到。
用量计量不在这里做：网关看不到会话，计量由宿主事件流里的 usage 承担。

路由：
- 给了模型目录（resolver）：按请求里的模型别名查 model 表，取对应厂商与解密后的密钥转发；
- 空间里一个模型都没配置，或没给 resolver：回退到配置里的固定上游（第一阶段的老路径）；
- 配置了模型但别名不存在或已停用：404，不回退，免得静默换成别的模型。
不经 LiteLLM：宿主的 dsh-llm-pi-ai 已统一协议，这里只换地址与密钥并按字节转发（理由见 models 包说明）。

上游出错时对宿主只回固定文案：上游的状态码与响应体只写服务端日志（可能含厂商账户信息）。
上游 401/403 是我们自己的密钥问题，对宿主表现为 502；429 原样保留，让适配器按限流重试。
单次调用有总时长上限与流式空闲上限（GatewayLimits），超时对宿主表现为 504 或截断的流。
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..kernel.tokens import TokenError, TokenSigner
from ..models.errors import NotFound, Unavailable
from ..models.schemas import Route
from .request_filter import InvalidRequest, parse_request

log = logging.getLogger(__name__)

MODEL_AUDIENCE = "model"
CHAT_PATH = "/chat/completions"
UPSTREAM_DETAIL_LOG_BYTES = 500


@dataclass(frozen=True)
class GatewayLimits:
    total_timeout_seconds: float = 300.0  # 单次调用从发出到流结束的上限
    idle_timeout_seconds: float = 60.0  # 两个流式块之间的最长等待
    connect_timeout_seconds: float = 10.0

    def httpx_timeout(self) -> httpx.Timeout:
        return httpx.Timeout(self.idle_timeout_seconds, connect=self.connect_timeout_seconds)


class RouteResolver(Protocol):
    """models.ModelCatalog 满足这个协议。None 表示空间没有配置任何模型。"""

    async def resolve(self, workspace_id: str, alias: str) -> Route | None: ...


@dataclass(frozen=True)
class _Outgoing:
    client: httpx.AsyncClient
    request: httpx.Request


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"message": message}})


def _status_for_host(upstream_status: int) -> int:
    if upstream_status == 429:
        return 429
    if upstream_status in (401, 403) or upstream_status >= 500:
        return 502
    return upstream_status


def _verify(signer: TokenSigner, header: str) -> str:
    """返回令牌里的工作空间。"""
    if not header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="invalid token")
    token = header[7:]
    # 签名比较在 tokens.py 里按 str 做；非 ASCII 的输入在这里先挡掉，避免比较时抛 TypeError。
    if not token.isascii():
        raise HTTPException(status_code=401, detail="invalid token")
    try:
        return signer.verify(token, audience=MODEL_AUDIENCE).workspace_id
    except TokenError as error:
        raise HTTPException(status_code=401, detail="invalid token") from error


async def _relay(response: httpx.Response, deadline: float) -> AsyncIterator[bytes]:
    loop = asyncio.get_running_loop()
    chunks = response.aiter_raw()
    try:
        while (remaining := deadline - loop.time()) > 0:
            try:
                chunk = await asyncio.wait_for(anext(chunks), remaining)
            except StopAsyncIteration:
                return
            yield chunk
        log.warning("model call exceeded total timeout; stream truncated")
    except TimeoutError:
        log.warning("model call exceeded total timeout; stream truncated")
    except httpx.HTTPError as error:
        log.warning("model upstream stream broken: %s", type(error).__name__)
    finally:
        await response.aclose()


class _Forwarder:
    def __init__(self, upstream: httpx.AsyncClient, upstream_model: str, resolver: RouteResolver | None,
                 http: httpx.AsyncClient | None, limits: GatewayLimits) -> None:
        if resolver is not None and (http is None or http is upstream):
            # 固定上游的客户端带着默认鉴权头，拿它转发到别的厂商会把那把密钥一起带过去
            raise ValueError("配置模型目录时必须给一个独立的、不带默认鉴权头的 http 客户端")
        self.upstream, self.upstream_model = upstream, upstream_model
        self.resolver, self.http, self.limits = resolver, http, limits

    async def build(self, workspace_id: str, body: dict[str, Any], alias: str | None) -> _Outgoing | JSONResponse:
        route = None
        if self.resolver is not None:
            if alias is None:
                return _error(400, "请求缺少模型名")
            try:
                route = await self.resolver.resolve(workspace_id, alias)
            except NotFound:
                return _error(404, "模型不存在或未启用")
            except Unavailable:
                log.warning("model route unavailable for alias %s", alias)
                return _error(503, "模型暂时不可用")
        timeout = self.limits.httpx_timeout()
        if route is None:
            if not self.upstream_model:
                return _error(404, "模型不存在或未启用")
            request = self.upstream.build_request("POST", CHAT_PATH, json={**body, "model": self.upstream_model},
                                                  timeout=timeout)
            return _Outgoing(self.upstream, request)
        headers = {"authorization": f"Bearer {route.api_key}"} if route.api_key else {}
        request = self.http.build_request("POST", route.base_url + CHAT_PATH,
                                          json={**body, "model": route.upstream_model}, headers=headers,
                                          timeout=timeout)
        return _Outgoing(self.http, request)

    async def send(self, outgoing: _Outgoing):
        total = self.limits.total_timeout_seconds
        deadline = asyncio.get_running_loop().time() + total
        try:
            response = await asyncio.wait_for(outgoing.client.send(outgoing.request, stream=True), total)
        except (TimeoutError, httpx.TimeoutException):
            log.warning("model upstream timed out")
            return _error(504, "上游模型响应超时")
        except httpx.HTTPError as error:
            log.warning("model upstream unreachable: %s", type(error).__name__)
            return _error(502, "上游模型暂时不可用")
        if response.status_code >= 400:
            detail = (await response.aread())[:UPSTREAM_DETAIL_LOG_BYTES].decode(errors="replace")
            await response.aclose()
            log.warning("model upstream returned %s: %s", response.status_code, detail)
            return _error(_status_for_host(response.status_code), "上游模型返回错误")
        return StreamingResponse(_relay(response, deadline), status_code=response.status_code,
                                 media_type=response.headers.get("content-type", "text/event-stream"))


def build_router(signer: TokenSigner, upstream: httpx.AsyncClient, upstream_model: str, *,
                 resolver: RouteResolver | None = None, http: httpx.AsyncClient | None = None,
                 limits: GatewayLimits | None = None) -> APIRouter:
    """upstream：固定上游（带 base_url 与厂商密钥头），作回退用；upstream_model 为空表示不回退。
    resolver：模型目录；给了就必须同时给 http（转发到各厂商用，不带 base_url 与默认鉴权头）。"""
    forwarder = _Forwarder(upstream, upstream_model, resolver, http, limits or GatewayLimits())
    router = APIRouter()

    @router.post("/internal/model/v1" + CHAT_PATH)
    async def chat_completions(request: Request):
        workspace_id = _verify(signer, request.headers.get("authorization", ""))
        try:
            body, alias = parse_request(await request.body())
        except InvalidRequest as error:
            return _error(400, str(error))
        built = await forwarder.build(workspace_id, body, alias)
        if isinstance(built, JSONResponse):
            return built
        return await forwarder.send(built)

    return router
