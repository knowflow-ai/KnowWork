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
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..kernel.tokens import TokenError, TokenSigner
from ..models.errors import NotFound, Unavailable
from ..models.schemas import Route
from ..models.thinking import with_thinking
from .request_filter import InvalidRequest, parse_request

log = logging.getLogger(__name__)

MODEL_AUDIENCE = "model"
CHAT_PATH = "/chat/completions"
UPSTREAM_DETAIL_LOG_BYTES = 500
# 流里出现真正的内容（正文、思考、工具调用）才算开始出字。有的模型服务一收到请求就先发一个只有 role、
# 内容为空的块，再去做长上下文的预填充，不能把那个空块当成开始
_CONTENT = re.compile(rb'"(?:content|reasoning_content|reasoning)"\s*:\s*"[^"]|"tool_calls"')
# 开始工具调用：自建推理服务的工具调用解析器常把一个参数整段攒完再发，写文件时脚本正文要生成一两分钟，
# 期间一个字节都没有（实测发出 file_path 后静默超过 60 秒）。所以工具调用开始后不再套空闲超时，只受总时长约束。
# 有的服务在普通正文块里也带 "tool_calls": null，要求后面跟数组才算
_TOOL_CALL = re.compile(rb'"tool_calls"\s*:\s*\[')


@dataclass(frozen=True)
class GatewayLimits:
    total_timeout_seconds: float = 300.0  # 单次调用从发出到流结束的上限
    idle_timeout_seconds: float = 60.0  # 开始出字后，两个流式块之间的最长等待（首块之前与工具调用开始后不算，见 _relay）
    connect_timeout_seconds: float = 10.0

    def httpx_timeout(self) -> httpx.Timeout:
        # 读超时只兜底总时长。流式接口先回响应头、等预填充完才出第一块：长上下文的自建大模型首字可能要一两分钟，
        # 读超时若按空闲超时设，就会把这段等待当成断流掐掉（实测 3 万 token 上下文报 ReadTimeout）
        return httpx.Timeout(self.total_timeout_seconds, connect=self.connect_timeout_seconds)


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


async def _relay(response: httpx.Response, deadline: float, idle: float) -> AsyncIterator[bytes]:
    """转发上游的流。出现第一块有内容的数据之前只受总时长约束（在等预填充）；开始出字后，块与块之间超过空闲超时就截断；
    开始工具调用后又只受总时长约束（参数可能被上游攒着发，见 _TOOL_CALL）。"""
    loop = asyncio.get_running_loop()
    chunks = response.aiter_raw()
    started = calling = False
    try:
        while (remaining := deadline - loop.time()) > 0:
            wait = min(idle, remaining) if started and not calling else remaining
            try:
                chunk = await asyncio.wait_for(anext(chunks), wait)
            except StopAsyncIteration:
                return
            except TimeoutError:
                if wait < remaining:
                    log.warning("model upstream stalled for %.0fs after streaming started; stream truncated", idle)
                    return
                raise
            started = started or bool(_CONTENT.search(chunk))
            calling = calling or bool(_TOOL_CALL.search(chunk))
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
        # 管理员给这个模型定了思考开、关时，按服务商的写法补上（Qwen3 等在自建服务上默认开着思考，回答慢）
        payload = with_thinking({**body, "model": route.upstream_model}, route.thinking, route.base_url)
        request = self.http.build_request("POST", route.base_url + CHAT_PATH, json=payload, headers=headers,
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
        return StreamingResponse(_relay(response, deadline, self.limits.idle_timeout_seconds),
                                 status_code=response.status_code,
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
