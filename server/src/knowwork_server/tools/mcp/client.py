"""连接 MCP 服务：列工具、调一次工具（官方 Python SDK 2.x，Streamable HTTP）。

- 每次操作新建一个连接，用完即关：MCP 服务的数量与调用频率都不高，省掉连接池与会话失效的处理；
- 请求头（鉴权）通过自建的 httpx2 客户端带上，SDK 只在同源时跟随重定向，凭据不会被带到别的主机；
- SDK 把 HTTP 错误和网络异常都吞成「服务端返回了错误」（异常链里也找不到原始异常），看不出是 401、
  404 还是连不上。这里在 HTTP 传输层记下最后一次请求的状态码或网络异常，翻译成管理员看得懂的原因；
  所有失败都转成 McpUnavailable，文案可以直接给人或模型看，不含内部细节；
- 工具结果照官方 dsh-mcp-client 的投影：文本块按换行拼接，资源链接写成一行，图片与音频给占位说明；
  只有结构化结果时输出它的 JSON。
"""

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

log = logging.getLogger(__name__)

MAX_TOOL_PAGES = 20
CONNECT_TIMEOUT_SECONDS = 10.0
MAX_ERROR_TEXT = 2000


class McpUnavailable(Exception):
    """连不上、被拒绝、超时或协议出错。message 面向管理员与模型。"""


@dataclass(frozen=True)
class Endpoint:
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float = 60.0


@dataclass(frozen=True)
class RemoteTool:
    name: str
    description: str
    input_schema: Any
    annotations: dict[str, Any] | None


@dataclass(frozen=True)
class CallOutcome:
    text: str
    is_error: bool


class _RecordingTransport(httpx2.AsyncHTTPTransport):
    """记下最后一次请求的结果：状态码，或者网络层抛出的异常。"""

    def __init__(self) -> None:
        super().__init__()
        self.status: int | None = None
        self.error: Exception | None = None

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        try:
            response = await super().handle_async_request(request)
        except Exception as error:
            self.status, self.error = None, error
            raise
        self.status, self.error = response.status_code, None
        return response


class McpClient:
    async def list_tools(self, endpoint: Endpoint) -> list[RemoteTool]:
        tools: list[RemoteTool] = []
        async with self._connect(endpoint) as client:
            cursor: str | None = None
            for _ in range(MAX_TOOL_PAGES):
                page = await client.list_tools(cursor=cursor, cache_mode="refresh")
                tools.extend(_remote(tool) for tool in page.tools)
                cursor = page.next_cursor
                if not cursor:
                    break
        return tools

    async def call_tool(self, endpoint: Endpoint, name: str, arguments: dict[str, Any]) -> CallOutcome:
        async with self._connect(endpoint) as client:
            result = await client.call_tool(name, arguments, read_timeout_seconds=endpoint.timeout_seconds)
        text = project(result.content, result.structured_content)
        return CallOutcome(text=text[:MAX_ERROR_TEXT] if result.is_error else text, is_error=bool(result.is_error))

    @asynccontextmanager
    async def _connect(self, endpoint: Endpoint) -> AsyncIterator[Client]:
        transport = _RecordingTransport()
        timeout = httpx2.Timeout(endpoint.timeout_seconds, connect=CONNECT_TIMEOUT_SECONDS)
        http = httpx2.AsyncClient(headers=endpoint.headers, timeout=timeout, transport=transport)
        try:
            async with http, Client(streamable_http_client(endpoint.url, http_client=http),
                                    read_timeout_seconds=endpoint.timeout_seconds, cache=None) as client:
                yield client
        except McpUnavailable:
            raise
        except Exception as error:
            reason = explain(error, transport.status, transport.error)
            log.warning("mcp request to %s failed: %s", endpoint.url, reason, exc_info=error)
            raise McpUnavailable(reason) from error


def explain(error: BaseException, status: int | None, network: Exception | None = None) -> str:
    if status in (401, 403):
        return f"MCP 服务拒绝了访问（HTTP {status}），请检查请求头里的凭据"
    if status == 404:
        return "MCP 服务地址不对（HTTP 404），请确认填的是 MCP 端点，一般以 /mcp 结尾"
    if status is not None and status >= 400:
        return f"MCP 服务返回了错误（HTTP {status}）"
    leaves = [*_leaves(error), *([network] if network is not None else [])]
    if any(isinstance(e, httpx2.TimeoutException | TimeoutError) for e in leaves):
        return "MCP 服务响应超时"
    if any(isinstance(e, httpx2.ConnectError) for e in leaves):
        return "连不上 MCP 服务，请检查地址和网络"
    if any(isinstance(e, httpx2.HTTPError) for e in leaves):
        return "和 MCP 服务通信失败，请检查地址和网络"
    return "MCP 服务的响应不符合协议，请确认地址指向一个 MCP 服务"


def _leaves(error: BaseException):
    if isinstance(error, BaseExceptionGroup):
        for inner in error.exceptions:
            yield from _leaves(inner)
        return
    yield error
    if error.__cause__ is not None:
        yield from _leaves(error.__cause__)


def _remote(tool: Any) -> RemoteTool:
    annotations = tool.annotations.model_dump(by_alias=True, exclude_none=True) if tool.annotations else None
    return RemoteTool(name=tool.name, description=tool.description or "", input_schema=tool.input_schema,
                      annotations=annotations)


def project(content: list[Any], structured: Any) -> str:
    lines: list[str] = []
    for block in content:
        kind = getattr(block, "type", None)
        if kind == "text":
            lines.append(block.text)
        elif kind == "resource_link":
            lines.append(f"资源链接：{block.name}（{block.uri}）")
        elif kind == "resource":
            text = getattr(block.resource, "text", None)
            lines.append(text if text is not None else f"[工具返回了一个二进制资源：{block.resource.uri}]")
        elif kind == "image":
            lines.append("[工具返回了一张图片，当前不支持查看]")
        elif kind == "audio":
            lines.append("[工具返回了一段音频，当前不支持收听]")
        else:
            lines.append("[工具返回了不支持的内容]")
    if not lines and structured is not None:
        return json.dumps(structured, ensure_ascii=False)
    return "\n".join(lines) if lines else "（工具没有返回内容）"
