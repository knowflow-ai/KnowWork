"""MCP 服务表（技术方案 7.1 节）。带 workspace_id，开启行级安全（登记在 db/registry.RLS_TABLES）。

请求头整体加密成一段密文（AES-256-GCM，用途 mcp-secrets），明文只在连接 MCP 服务的那一刻出现在内存里；
header_names 只存请求头名字与值的末四位，给管理界面展示用。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
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
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sync_error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(ID)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())
