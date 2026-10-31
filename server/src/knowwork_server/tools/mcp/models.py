"""MCP 服务表与每人的连接。都带 workspace_id，开启行级安全（登记在 db/registry.RLS_TABLES）。

请求头整体加密成一段密文（AES-256-GCM，用途 mcp-secrets），明文只在连接 MCP 服务的那一刻出现在内存里；
header_names 只存请求头名字与值的末四位，给管理界面展示用。

认证方式（auth）：shared 共享凭据（用上面那份请求头），user_token 个人访问令牌，oauth OAuth 授权。
每人方式下每个成员一条 mcp_connection：令牌加密存（附加数据绑定空间、服务与用户），display 只给人看（后四位）。
OAuth 授权时 auth_config 存发现到的授权服务器地址（不含密钥），client 加密存在 oauth_client_ciphertext；
还没走完的授权记在 mcp_oauth_pending。

范围（scope）：workspace 是空间管理员登记的；enterprise 是企业 MCP（系统管理员登记，存在平台配置所在的默认空间名下，
audience 记发给哪些空间，"*" 是全部空间）。企业服务的 name 存成 ent_<名字>：空间服务名不允许下划线，工具名前缀
mcp__ent_<名字>__ 不会和任何空间服务撞，原来的（空间, 名字）唯一约束也不用改。它的每人连接同样存在默认空间名下，
所以一个人只有一份、换空间不用重连。mcp_workspace_setting 记空间管理员对到达本空间的企业服务的设置（停用、收窄给谁用）。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, false, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ...db.base import Base

ID = String(64)


class McpServerRow(Base):
    __tablename__ = "mcp_server"
    __table_args__ = (UniqueConstraint("workspace_id", "name", name="uk_mcp_server_name"),)

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(24))          # 工具名前缀 mcp__<name>__，建好不能改
    title: Mapped[str] = mapped_column(String(100))         # 给人看的名称
    url: Mapped[str] = mapped_column(String(1000))
    headers_ciphertext: Mapped[str | None] = mapped_column(Text)
    header_names: Mapped[list] = mapped_column(JSONB, default=list)  # [{"name": ..., "last4": ...}]
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=60)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    tools: Mapped[list] = mapped_column(JSONB, default=list)  # 工具清单快照，见 catalog.py
    auth: Mapped[str] = mapped_column(String(16), default="shared", server_default="shared")  # shared | user_token | oauth
    # 个人访问令牌：{"header": 请求头名, "prefix": 值前缀, "help": 给成员看的「去哪拿令牌」}；
    # OAuth 授权：发现到的授权服务器（oauth.AuthServer）与 scope；共享凭据为空
    auth_config: Mapped[dict | None] = mapped_column(JSONB)
    oauth_client_ciphertext: Mapped[str | None] = mapped_column(Text)  # OAuth 授权的 client（oauth.OAuthClient）
    scope: Mapped[str] = mapped_column(String(16), default="workspace", server_default="workspace")  # workspace | enterprise
    audience: Mapped[list | None] = mapped_column(JSONB)  # 企业服务发给哪些空间："*" 是全部空间；空间服务为空
    origin_server_id: Mapped[str | None] = mapped_column(ID)  # 从哪个空间服务收上来的（那个空间据此提醒可以删掉原来那份）
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sync_error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(ID)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


class McpConnectionRow(Base):
    __tablename__ = "mcp_connection"

    server_id: Mapped[str] = mapped_column(ForeignKey("mcp_server.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # token | oauth
    secret_ciphertext: Mapped[str] = mapped_column(Text)
    display: Mapped[str] = mapped_column(String(100), default="")  # 给人看的：令牌后四位或对方账号名
    status: Mapped[str] = mapped_column(String(16), default="ok")  # ok | invalid（对方拒绝过，要重新连接）
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


class McpOAuthPendingRow(Base):
    """还没走完的 OAuth 授权：点「连接」时记一条，回调时按 state 摘要找到并删掉（只能用一次），过期的下次发起时顺手清。"""

    __tablename__ = "mcp_oauth_pending"

    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)
    server_id: Mapped[str] = mapped_column(ForeignKey("mcp_server.id", ondelete="CASCADE"))
    user_id: Mapped[str] = mapped_column(ID)  # 发起人；回调必须是他本人的登录会话
    verifier_ciphertext: Mapped[str] = mapped_column(Text)  # PKCE 校验串
    return_to: Mapped[str] = mapped_column(String(500))  # 走完后回到的页面（站内路径）
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class McpWorkspaceSettingRow(Base):
    """空间管理员对一个到达本空间的企业服务的设置，只存和默认（启用、全体成员可用）不同的。收窄时选中的对象存成本空间对
    tool:mcp__ent_<名字>__* 的「使用」授权；单独记 narrowed，收窄到的部门被删时只会更窄、不会放大成全体（同企业技能）。
    企业服务删掉时随外键一起删（各个空间的行都删，外键不受行级安全限制）。"""

    __tablename__ = "mcp_workspace_setting"

    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), primary_key=True)
    server_id: Mapped[str] = mapped_column(ForeignKey("mcp_server.id", ondelete="CASCADE"), primary_key=True)
    off: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    narrowed: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    updated_by: Mapped[str] = mapped_column(ID)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
