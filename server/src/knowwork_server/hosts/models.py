"""对话落在哪个宿主。一行 = 这段对话的去处已经定了；host_user 为空是共享宿主，否则是这个人的专属宿主。

这张表不带 workspace_id、不做行级安全：按对话转发调用时（事件泵、凭据轮换这些后台任务）手里只有对话编号，
不一定在某个空间的上下文里；里面也只有编号，没有业务内容。
"""

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base


class SessionHost(Base):
    __tablename__ = "session_host"

    session_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    host_workspace: Mapped[str | None] = mapped_column(String(64))
    host_user: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
