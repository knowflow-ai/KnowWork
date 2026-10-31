"""MCP 工具接入：业务服务用官方 Python SDK 连 MCP 服务（Streamable HTTP），
请求头加密存库，工具接在 ToolRegistry 接口后面，审批、动作回执、审计、授权都沿用工具桥。

不用 DSH 官方的 dsh-mcp-client：它把凭据写在宿主配置里、由宿主直接调用 MCP 服务，绕开了业务服务的
授权、动作回执与审计，违背「DSH 里不放密钥、不做权限判断」。工具命名与结果投影照搬它的做法。
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import async_sessionmaker

from ...knowledge.crypto import SecretBox
from .admin import McpAdmin
from .client import McpClient
from .connections import McpConnections
from .enterprise import EnterpriseMcpAdmin, EnterpriseMcpHere, McpReach
from .me import McpMe, Permits
from .oauth import OAuthProtocol
from .oauth_login import McpOAuthLogin
from .registry import McpToolRegistry
from .store import SECRET_PURPOSE, McpServerStore

CALLBACK_PATH = "/api/mcp/oauth/callback"


@dataclass(frozen=True)
class McpModule:
    admin: McpAdmin
    registry: McpToolRegistry
    connections: McpConnections  # 每人的连接；移出成员时 forget_member
    me: McpMe  # 「设置 → 连接」
    login: McpOAuthLogin  # OAuth 授权的回调
    enterprise: EnterpriseMcpAdmin  # 系统管理员管企业 MCP（给扩展的接口与页面用）
    here: EnterpriseMcpHere  # 空间管理员对到达本空间的企业 MCP：停用、收窄给谁用


def build_mcp(master_key: str, sessionmaker: async_sessionmaker, access, *, public_base_url: str,
              permits: Permits | None = None, client: McpClient | None = None,
              protocol: OAuthProtocol | None = None) -> McpModule:
    """permits：(用户, 空间, 工具名) → 能不能用，「设置 → 连接」只列本人能用的服务；不给时按空间成员算。
    public_base_url：浏览器访问的外部地址，OAuth 授权的回调地址由它拼出。"""
    box = SecretBox.from_master_key(master_key, SECRET_PURPOSE)
    store = McpServerStore(sessionmaker, box)
    connections = McpConnections(sessionmaker, box)
    client = client or McpClient()
    login = McpOAuthLogin(sessionmaker, box, store, connections, client,
                          redirect_uri=public_base_url.rstrip("/") + CALLBACK_PATH, protocol=protocol)
    check = permits or (lambda user_id, workspace_id, _name: access.check(user_id, workspace_id, "workspace", "use"))
    reach = McpReach(sessionmaker, store, access)
    return McpModule(admin=McpAdmin(store, client, access, connections, login),
                     registry=McpToolRegistry(store, client, connections, login, reach), connections=connections,
                     me=McpMe(store, client, connections, access, check, login, reach), login=login,
                     enterprise=EnterpriseMcpAdmin(store, client, access, connections, login),
                     here=EnterpriseMcpHere(sessionmaker, reach, access))


__all__ = ["CALLBACK_PATH", "McpModule", "build_mcp"]
