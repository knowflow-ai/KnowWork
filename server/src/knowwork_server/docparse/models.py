"""文档解析服务的配置（管理后台「模型 → 文档解析」）：每个工作空间一行，有这一行就以它为准，没有就用部署时的
环境变量（KNOWWORK_MINERU_BASE_URL 等）。开行级安全。"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base

ID = String(64)


class DocumentParserRow(Base):
    __tablename__ = "document_parser"

    workspace_id: Mapped[str] = mapped_column(ID, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), default="mineru")  # mineru：自己部署的 MinerU；以后加官方在线 API
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    base_url: Mapped[str] = mapped_column(String(500))
    # VLM 推理服务（sglang / vllm，常见 30000 端口）：MinerU 的 vlm、hybrid 后端去访问它，是 MinerU 那台机器看到的地址
    server_url: Mapped[str | None] = mapped_column(String(500))
    # 解析模式：auto（知识库 hybrid、附件 pipeline）或强制 vlm-http-client / hybrid-http-client / pipeline
    backend: Mapped[str] = mapped_column(String(32), default="auto")
    api_key_ciphertext: Mapped[str | None] = mapped_column(Text)  # 服务前面有网关要令牌时才填，加密存
    api_key_last4: Mapped[str | None] = mapped_column(String(4))
    lang: Mapped[str] = mapped_column(String(16), default="ch")
    max_concurrency: Mapped[int] = mapped_column(Integer, default=3)
    updated_by: Mapped[str | None] = mapped_column(ID)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


DOCPARSE_TABLES = (DocumentParserRow.__table__,)
DOCPARSE_RLS_TABLES = ("document_parser",)
