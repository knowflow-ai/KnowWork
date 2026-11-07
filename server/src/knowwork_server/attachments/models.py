"""附件表。带 workspace_id，开启行级安全（ATTACHMENT_RLS_TABLES）。解析任务在通用任务表里（jobs，类型 attachment.parse）。

- attachment.digest 是原件摘要，parsed_digest 是 MinerU 产出的 Markdown 摘要，都在对象存储里（命名空间见 store_scope）；
- input_name 是原件放进沙箱 /workspace/input 的文件名（照官方 dsh-attachment：任何文件都原样保存，模型按需读），
  parsed_name 是 MinerU 解析出的 Markdown 的文件名（只有配了 MinerU 的 PDF、Word、PPT 才有），两者会话内唯一；
- 文件先有、对话后建：输入框里一选文件就传上来（conversation_id 为空，挂在 user_id 名下，status 为 staged），
  打字的工夫就解析好了；发消息时认领进对话（conversation_id 填上），再放进那个对话的沙箱。一直没发出去的到期删掉；
- store_scope 是原件与 Markdown 在对象存储里的命名空间：每个附件用自己的 id，认领进对话时文件不用搬，删一个附件也碰不到别的；
  为空的是 2026-11-06 之前的行，用的是对话 id；
- status 管原件：staged（还没发出去）→ placing → ready（已放进沙箱）| place_failed（内容在，放不进沙箱）；
- parse_status 管解析：空（不解析）| parsing → ready | failed。解析失败不影响原件可用。
- 导读（digest_job.py）：文字放不进对话的长文档，解析完在后台按段并行摘要、再汇总成全文摘要，存成 digest_name 那份
  Markdown 放在旁边；digest_status 空（不做）| digesting → ready | failed，digest_done / digest_total 是做到第几段。
  导读失败不影响原件与解析结果。
- page_count 是 PDF 的页数（pdftotext 每页一个换页符，扫描件也数得出来）：告诉模型文件一共几页、给的就是全部文字，
  免得它怀疑没读全、自己再去转原件。其他格式为空。
  2026-10-20 之前的行（迁移 0013 之前）这三类只放了 Markdown：parsed_name 等于 input_name，原件不在沙箱里。
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base

ID = String(64)


class Attachment(Base):
    __tablename__ = "attachment"
    __table_args__ = (UniqueConstraint("conversation_id", "input_name", name="uq_attachment_input_name"),)

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ID, index=True)
    conversation_id: Mapped[str | None] = mapped_column(ForeignKey("conversation.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ID)
    store_scope: Mapped[str | None] = mapped_column(ID)
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
    digest_name: Mapped[str | None] = mapped_column(String(200))
    digest_digest: Mapped[str | None] = mapped_column(String(64))
    digest_status: Mapped[str | None] = mapped_column(String(16))
    digest_done: Mapped[int | None] = mapped_column(Integer)
    digest_total: Mapped[int | None] = mapped_column(Integer)
    page_count: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


ATTACHMENT_TABLES = (Attachment.__table__,)
ATTACHMENT_RLS_TABLES = ("attachment",)
