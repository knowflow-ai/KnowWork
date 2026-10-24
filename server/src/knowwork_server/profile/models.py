"""运行配置的持久化（kernel_binding、runtime_profile）。

- runtime_profile：按内容寻址的配置快照，主键 (workspace_id, version)；同一份配置只存一行，供审计与回看；
- kernel_binding：会话当前绑定的配置版本。选单独一张表而不是给 conversation 加列：
  它描述的是「会话与宿主运行时的对应关系」，以后还要放宿主运行时、宿主会话 id，归运行时而不归对话；
  会话删除时随外键级联删除。
两张表都带 workspace_id，需开行级安全。
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base


class RuntimeProfileRow(Base):
    __tablename__ = "runtime_profile"

    workspace_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    snapshot: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KernelBinding(Base):
    __tablename__ = "kernel_binding"

    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversation.id", ondelete="CASCADE"), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    profile_version: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


PROFILE_TABLES = (RuntimeProfileRow.__table__, KernelBinding.__table__)
PROFILE_RLS_TABLES = ("runtime_profile", "kernel_binding")
