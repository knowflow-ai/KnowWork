"""测试用的真实 MCP 服务（官方 SDK 的 MCPServer，Streamable HTTP），要求 Authorization 请求头。

工具：
- add：只读注解；
- create_ticket：没写注解（按写入处理），参数带枚举、可空、默认值（MCP 常见写法，验证 DSH 能接受）；
- delete_item：声明破坏性，总是报错（验证 isError 的原因能带回去）；
- kb.search：名字带点，对外名字要规范化并补哈希。
"""

import asyncio
from typing import Literal

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

TOKEN = "Bearer mcp-test-token-1234"  # 传空字符串表示不要求鉴权


def build_mcp_app(calls: list[tuple[str, dict]], *, token: str = TOKEN):
    server = MCPServer("knowwork-test")

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def add(a: int, b: int) -> int:
        """两数相加"""
        calls.append(("add", {"a": a, "b": b}))
        return a + b

    @server.tool()
    def create_ticket(title: str, priority: Literal["low", "high"] = "low", assignee: str | None = None) -> str:
        """新建一张工单"""
        calls.append(("create_ticket", {"title": title, "priority": priority, "assignee": assignee}))
        return f"已创建工单 T-{len(calls)}：{title}（{priority}）"

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True))
    def delete_item(item_id: str) -> str:
        """删除一条记录"""
        raise ToolError(f"记录 {item_id} 不存在")

    def search(query: str) -> str:
        return f"找到 3 条与「{query}」相关的结果"

    server.add_tool(search, name="kb.search", description="检索知识库")
    inner = server.streamable_http_app()

    async def app(scope, receive, send):
        if token and scope["type"] == "http" and dict(scope["headers"]).get(b"authorization") != token.encode():
            await send({"type": "http.response.start", "status": 401, "headers": [(b"content-type", b"text/plain")]})
            await send({"type": "http.response.body", "body": b"unauthorized"})
            return
        await inner(scope, receive, send)

    return app


class McpStub:
    """在当前事件循环里起 MCP 服务；MCP 的会话管理器要 lifespan，所以不复用 support.Served。"""

    def __init__(self, port: int, *, token: str = TOKEN) -> None:
        self.port = port
        self.calls: list[tuple[str, dict]] = []
        config = uvicorn.Config(build_mcp_app(self.calls, token=token), host="127.0.0.1", port=port,
                                log_level="warning", lifespan="on")
        self.server = uvicorn.Server(config)
        self.task: asyncio.Task | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"

    async def __aenter__(self):
        self.task = asyncio.create_task(self.server.serve())
        while not self.server.started:
            await asyncio.sleep(0.02)
        return self

    async def __aexit__(self, *exc):
        self.server.should_exit = True
        await self.task
