"""产物表。带 workspace_id，开启行级安全（ARTIFACT_RLS_TABLES）。

- digest 是存进对象存储的内容摘要（表格是转义之后的内容）；source_sha256 是沙箱里原始文件的摘要，
  用来认出「上一轮已经收过、这一轮没变」的文件，不重复收；
- 逻辑键 {workspace}/{session}/{artifact_id}/{filename} 由 workspace_id、conversation_id、id、filename 四列给出；
- preview_digest 是预览用 PDF 在同一会话存储里的摘要（Word、PPT 第一次预览时在沙箱里转换，之后直接取）。
"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base

ID = String(64)


class Artifact(Base):
    __tablename__ = "artifact"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ID, index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversation.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str | None] = mapped_column(ID)
    filename: Mapped[str] = mapped_column(String(200))
    content_type: Mapped[str] = mapped_column(String(128))
    size: Mapped[int] = mapped_column(BigInteger)
    digest: Mapped[str] = mapped_column(String(64))
    source_sha256: Mapped[str] = mapped_column(String(64))
    sanitized: Mapped[bool] = mapped_column(Boolean, default=False)  # 表格是否做过公式转义
    preview_digest: Mapped[str | None] = mapped_column(String(64))
    # 模型 present 时写的说明（收成一行，最长 300 字，见 collector.one_line），卡片上显示；没写是 None
    description: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


ARTIFACT_TABLES = (Artifact.__table__,)
ARTIFACT_RLS_TABLES = ("artifact",)
