"""动作回执。

- action_id 与 idempotency_key 各自唯一。幂等键 = 根会话 ID + 宿主给出的「实际会话 ID:调用 ID」。
- 同一幂等键再来一次，返回第一次的结果，不重复执行；同一幂等键换了工具或参数，直接拒绝。
- 结果超过 64KB 时全文存到 action_result_blob，回执只存引用并标记 truncated。
  第一阶段没有对象存储，引用指向这张表；接入对象存储时只换 ResultStore 的实现。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base


class ActionReceipt(Base):
    __tablename__ = "action_receipt"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    action_id: Mapped[str] = mapped_column(String(64), unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(512), unique=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    session_id: Mapped[str] = mapped_column(String(128), index=True)
    agent_session_id: Mapped[str] = mapped_column(String(128))
    tool_name: Mapped[str] = mapped_column(String(64))
    arguments_digest: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))  # running | succeeded | failed
    result_digest: Mapped[str | None] = mapped_column(String(64))
    result_inline: Mapped[str | None] = mapped_column(Text)
    result_ref: Mapped[str | None] = mapped_column(String(64))
    truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


class ActionResultBlob(Base):
    """超过内联上限的完整结果。"""

    __tablename__ = "action_result_blob"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
