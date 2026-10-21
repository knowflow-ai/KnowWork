"""初始化状态表：全局单行（id 恒为 1），不开行级安全（默认空间 id 记在 default_workspace_id，不是隔离键）。

这一行既是初始化的并发锁（SELECT ... FOR UPDATE），也记录首个系统管理员是谁。
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base

SINGLETON_ID = 1


class SystemSetup(Base):
    __tablename__ = "system_setup"
    __table_args__ = (CheckConstraint("id = 1", name="ck_system_setup_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    admin_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    default_workspace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


SETUP_TABLES = (SystemSetup.__table__,)
# 全局表，不需要行级安全
SETUP_RLS_TABLES: tuple[str, ...] = ()
