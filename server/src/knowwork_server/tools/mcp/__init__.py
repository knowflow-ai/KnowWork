"""MCP 工具接入（技术方案 7.1 节）：业务服务用官方 Python SDK 连 MCP 服务（Streamable HTTP），
请求头加密存库，工具接在 ToolRegistry 接口后面，审批、动作回执、审计、授权都沿用工具桥。

不用 DSH 官方的 dsh-mcp-client：它把凭据写在宿主配置里、由宿主直接调用 MCP 服务，绕开了业务服务的
授权、动作回执与审计，违背「DSH 里不放密钥、不做权限判断」。工具命名与结果投影照搬它的做法。
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import async_sessionmaker

from ...knowledge.crypto import SecretBox
from .admin import McpAdmin
from .client import McpClient
from .registry import McpToolRegistry
from .store import SECRET_PURPOSE, McpServerStore


@dataclass(frozen=True)
class McpModule:
    admin: McpAdmin
    registry: McpToolRegistry


def build_mcp(master_key: str, sessionmaker: async_sessionmaker, access, *,
              client: McpClient | None = None) -> McpModule:
    store = McpServerStore(sessionmaker, SecretBox.from_master_key(master_key, SECRET_PURPOSE))
    client = client or McpClient()
    return McpModule(admin=McpAdmin(store, client, access), registry=McpToolRegistry(store, client))


__all__ = ["McpModule", "build_mcp"]
