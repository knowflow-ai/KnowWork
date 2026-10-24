"""审计日志表。带 workspace_id，开启行级安全（AUDIT_RLS_TABLES）。

只存「谁、经由什么凭证、对什么、做了什么、结果如何」：不存提示词、回答原文、密钥、文档内容；
detail 是经过脱敏与限长的小字典（见 events.py）。凭证只存会话或 API Key 的 id，不存明文。
"""

from datetime import datetime

from sqlalchemy import DateTime, Index, SmallInteger, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base

ID = String(64)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ID)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actor_type: Mapped[str] = mapped_column(String(16))           # human | agent | system
    actor_id: Mapped[str | None] = mapped_column(ID)              # 用户 id；匿名请求（如登录失败）为空
    credential_type: Mapped[str | None] = mapped_column(String(16))  # session | api_key | oidc | tool_token
    credential_id: Mapped[str | None] = mapped_column(ID)         # 会话或 API Key 的 id，绝不是明文
    action: Mapped[str] = mapped_column(String(64))
    resource_type: Mapped[str | None] = mapped_column(String(64))
    resource_id: Mapped[str | None] = mapped_column(String(200))
    result: Mapped[str] = mapped_column(String(16))               # success | denied | failed
    status_code: Mapped[int | None] = mapped_column(SmallInteger)
    ip: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(128))
    session_id: Mapped[str | None] = mapped_column(String(128))   # Agent 操作或会话内操作所在的会话
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)

    __table_args__ = (
        Index("ix_audit_log_workspace_time", "workspace_id", "occurred_at", "id"),
        Index("ix_audit_log_workspace_actor_time", "workspace_id", "actor_id", "occurred_at"),
        Index("ix_audit_log_workspace_action_time", "workspace_id", "action", "occurred_at"),
    )


AUDIT_TABLES = (AuditLog.__table__,)
AUDIT_RLS_TABLES = ("audit_log",)
