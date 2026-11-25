"""内容安全策略：每个工作空间一行（管理后台「运营 → 内容安全」），按工作空间做行级安全。

没有这一行就是这个空间没开（不建检查、不写审计）。word_sources 是勾了哪几种词表（custom 是自定义词表，其余是扩展登记的
来源，见 sources.py）；自定义的敏感词存规范化以后的文本，一行一个；改动记一条审计
（content_safety.policy_update），不另建历史表。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base

ID = String(64)


class ContentSafetyPolicyRow(Base):
    __tablename__ = "content_safety_policy"

    workspace_id: Mapped[str] = mapped_column(ID, primary_key=True)
    # 每次保存加一；保存时带上打开页面时的版本，对不上就是别的管理员改过
    version: Mapped[int] = mapped_column(Integer)
    input_enabled: Mapped[bool] = mapped_column(Boolean)
    output_enabled: Mapped[bool] = mapped_column(Boolean)
    word_sources: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[\"custom\"]'::jsonb"))
    words: Mapped[str] = mapped_column(Text)
    block_message: Mapped[str] = mapped_column(String(500))
    updated_by: Mapped[str | None] = mapped_column(ID)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())
