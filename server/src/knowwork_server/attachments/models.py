"""附件表。带 workspace_id，开启行级安全（ATTACHMENT_RLS_TABLES）。解析任务在通用任务表里（jobs，类型 attachment.parse）。

- attachment.digest 是原件摘要，parsed_digest 是 MinerU 产出的 Markdown 摘要，都在会话命名空间的对象存储里；
- input_name 是原件放进沙箱 /workspace/input 的文件名（照官方 dsh-attachment：任何文件都原样保存，模型按需读），
  parsed_name 是 MinerU 解析出的 Markdown 的文件名（只有配了 MinerU 的 PDF、Word、PPT 才有），两者会话内唯一；
- status 管原件：placing → ready（已放进沙箱）| place_failed（内容在，放不进沙箱）；
- parse_status 管解析：空（不解析）| parsing → ready | failed。解析失败不影响原件可用。
  2026-10-20 之前的行（迁移 0013 之前）这三类只放了 Markdown：parsed_name 等于 input_name，原件不在沙箱里。
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base

ID = String(64)


class Attachment(Base):
    __tablename__ = "attachment"
    __table_args__ = (UniqueConstraint("conversation_id", "input_name", name="uq_attachment_input_name"),)

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ID, index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversation.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ID)
    filename: Mapped[str] = mapped_column(String(200))
    input_name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(16))
    mime: Mapped[str] = mapped_column(String(128))
    size: Mapped[int] = mapped_column(BigInteger)
    digest: Mapped[str] = mapped_column(String(64))
    parsed_digest: Mapped[str | None] = mapped_column(String(64))
    parsed_name: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16))
    parse_status: Mapped[str | None] = mapped_column(String(16))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


ATTACHMENT_TABLES = (Attachment.__table__,)
ATTACHMENT_RLS_TABLES = ("attachment",)
