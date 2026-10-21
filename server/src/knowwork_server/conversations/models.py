"""会话与事件的持久化模型。第一阶段先只有默认工作空间，表结构从一开始就带 workspace_id。"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base


class Conversation(Base):
    __tablename__ = "conversation"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # 最近一次发消息或改名的时间，会话列表按它倒序
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class RunEvent(Base):
    """宿主事件的持久副本，是界面回放的唯一来源。

    - seq 是会话内单调递增的序号，由事件泵在推送前分配（落库是批量异步的），
      主键 (conversation_id, seq) 同时就是回放用的复合索引；
    - data 是宿主原始帧的 data，完整保存；ui 是这一帧投影出的 events.v1 事件（可能为空列表）；
    - kind 为 kernel.invalid 表示这帧存不下或格式不对，只留占位；kernel.gap 表示宿主缓冲已丢帧。
    """

    __tablename__ = "run_event"
    __table_args__ = (UniqueConstraint("conversation_id", "host_epoch", "host_cursor"),)

    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversation.id", ondelete="CASCADE"), primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    workspace_id: Mapped[str] = mapped_column(String(64))
    host_epoch: Mapped[str] = mapped_column(String(64))
    host_cursor: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict | list | None] = mapped_column(JSONB)
    ui: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
