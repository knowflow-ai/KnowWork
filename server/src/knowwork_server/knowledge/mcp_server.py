"""对外检索的 MCP 服务端：把列出知识库、检索、阅读包成三个 MCP 工具，挂在 /api/v1/mcp。

- 用官方 MCP SDK 的无状态流式 HTTP（stateless、JSON 响应）：每个请求都带 API Key，在外层现场判定身份
  （要 knowledge:read，且是工作空间成员），不在服务端缓存会话与身份，避免串号；
- 没通过判定的请求连工具列表都拿不到（直接 401 / 403）；
- 官方 SDK 默认按本机服务开 DNS 重绑定防护，只放行 localhost 的 Host 头；部署在网关后面会挡掉真实域名，
  这里每次请求都核对 API Key，所以关掉它；
- 会话管理器要在应用启动时跑起来（start / stop 由运行时调）。
"""

import json
import logging
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from typing import Any

from fastapi import HTTPException
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.streamable_http_manager import StreamableHTTPASGIApp
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from .errors import KnowledgeError
from .models import MetaFilter
from .reader import KnowledgeReader, ReadBody, SearchBody

log = logging.getLogger(__name__)
PATH = "/api/v1/mcp"
Authenticate = Callable[[Request], Awaitable[Any]]  # 返回 Actor（user_id、workspace_id），不通过抛 HTTPException


class KnowledgeMcp:
    def __init__(self, reader: KnowledgeReader, authenticate: Authenticate) -> None:
        self._reader = reader
        self._authenticate = authenticate
        self._stack: AsyncExitStack | None = None
        server = MCPServer(name="KnowWork 知识检索", instructions=(
            "检索企业知识库。先用 knowledge_bases 看能查哪些库、有哪些元数据字段，再用 knowledge_search 检索，"
            "需要上下文时用 knowledge_read 从结果里的 span_ref 接着读。回答时注明出处（title、locator）。"))
        self._register(server)
        server.streamable_http_app(json_response=True, stateless_http=True,
                                   transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
        self._server = server
        self._handler = StreamableHTTPASGIApp(server.session_manager)

    def _register(self, server: MCPServer) -> None:
        reader = self._reader

        async def caller_of(ctx: Context):
            actor = ctx.request_context.request.state.knowwork_actor
            return await reader.service.caller(actor.workspace_id, actor.user_id)

        async def run(call) -> str:
            try:
                return json.dumps(await call, ensure_ascii=False)
            except KnowledgeError as error:
                raise ToolError(str(error)) from error

        @server.tool(description="列出能检索的知识库：说明、元数据字段（类型、可选值）、标签，写检索条件之前用")
        async def knowledge_bases(ctx: Context, knowledge_bases: list[str] | None = None) -> str:
            return await run(reader.bases(await caller_of(ctx), knowledge_bases))

        @server.tool(description="在能查的知识库里检索，返回带定位与原文的片段。编号、型号这类要原样命中的用 mode=keyword；"
                                 "知识库支持时（knowledge_bases 的 routes）可以用 multi_hop、visual 打开或关掉多跳、视觉检索")
        async def knowledge_search(ctx: Context, query: str, top_n: int = 8, knowledge_bases: list[str] | None = None,
                                   mode: str = "hybrid", folders: list[str] | None = None,
                                   tags: list[str] | None = None, filters: list[dict] | None = None,
                                   multi_hop: bool | None = None, visual: bool | None = None) -> str:
            body = SearchBody(query=query, top_n=top_n, knowledge_bases=knowledge_bases, mode=mode,
                              folders=folders or [], tags=tags or [],
                              filters=[MetaFilter.model_validate(f) for f in filters or []],
                              multi_hop=multi_hop, visual=visual)
            return await run(reader.search(await caller_of(ctx), body))

        @server.tool(description="按文档引用连续阅读原文；chunk 填检索结果的 span_ref 从那里读，用 next_cursor 续读")
        async def knowledge_read(ctx: Context, source_id: str, doc_ref: str, cursor: str | None = None,
                                 chunk: str | None = None, section: str | None = None, find: str | None = None) -> str:
            body = ReadBody(source_id=source_id, doc_ref=doc_ref, cursor=cursor, chunk=chunk, section=section,
                            find=find)
            return await run(reader.read(await caller_of(ctx), body))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """先判身份再交给 MCP；身份放进这次请求的 state，工具从那里取。"""
        request = Request(scope, receive)
        try:
            actor = await self._authenticate(request)
        except HTTPException as error:
            await JSONResponse({"detail": error.detail}, status_code=error.status_code)(scope, receive, send)
            return
        scope.setdefault("state", {})["knowwork_actor"] = actor
        await self._handler(scope, receive, send)

    async def start(self) -> None:
        stack = AsyncExitStack()
        await stack.enter_async_context(self._server.session_manager.run())
        self._stack = stack

    async def stop(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None
