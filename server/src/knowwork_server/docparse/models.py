"""文档解析服务的配置（管理后台「模型 → 文档解析」）：全平台共用一行，存在平台配置所在的空间名下
（PLATFORM_WORKSPACE_ID）；有这一行就以它为准，没有就用部署时的环境变量（KNOWWORK_MINERU_BASE_URL 等）。
表结构按空间留着（开行级安全），以后要按空间区分不用改表。"""

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
    # 解析档位（MinerU 4.1 的 tier）：auto 是让 MinerU 自动推荐，也可以强制 flash / basic / standard / advanced
    tier: Mapped[str] = mapped_column(String(16), default="auto")
    api_key_ciphertext: Mapped[str | None] = mapped_column(Text)  # 服务前面有网关要令牌时才填，加密存
    api_key_last4: Mapped[str | None] = mapped_column(String(4))
    max_concurrency: Mapped[int] = mapped_column(Integer, default=3)
    updated_by: Mapped[str | None] = mapped_column(ID)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


DOCPARSE_TABLES = (DocumentParserRow.__table__,)
DOCPARSE_RLS_TABLES = ("document_parser",)
