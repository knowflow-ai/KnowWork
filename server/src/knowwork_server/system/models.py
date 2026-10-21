"""系统级设置：品牌（名称、Logo）。全局单行（id 恒为 1），和 system_setup 一样不分工作空间、不开行级安全。

品牌是整套部署的门面（登录页、浏览器标签、侧栏），不属于哪个工作空间；只有系统管理员能改。
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base

SINGLETON_ID = 1


class SystemBrand(Base):
    __tablename__ = "system_brand"
    __table_args__ = (CheckConstraint("id = 1", name="ck_system_brand_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 为空表示用默认名称 KnowWork
    name: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Logo 很小（上限 512 KB），直接存库，省得为一张图接对象存储；为空表示用默认 Logo
    logo: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    logo_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)


SYSTEM_TABLES = (SystemBrand.__table__,)
