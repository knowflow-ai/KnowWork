"""知识模块的表。都带 workspace_id，开启行级安全（KNOWLEDGE_RLS_TABLES）。

密钥字段只存 AES-256-GCM 密文与末四位；明文只在调用知识源的那一刻在内存里出现。
user_id 暂不加外键，与 access 模块一致，整合时统一补。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base

ID = String(64)


class KnowledgeSourceRow(Base):
    __tablename__ = "knowledge_source"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ID, index=True)
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(32))            # 目前只有 knowflow
    base_url: Mapped[str] = mapped_column(String(500))
    config: Mapped[dict] = mapped_column(JSONB, default=dict)  # 非敏感配置：身份模式、知识库范围、检索参数
    secret_ciphertext: Mapped[str | None] = mapped_column(Text)  # 服务密钥（签名断言模式）
    secret_last4: Mapped[str] = mapped_column(String(4), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str] = mapped_column(ID)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


class CitationCatalogRow(Base):
    """一轮的引用目录头：条数、是否截断、丢了几条。catalog_id 由调用方给出（目前是会话 ID）。"""

    __tablename__ = "citation_catalog"

    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ID, index=True)
    size: Mapped[int] = mapped_column(Integer, default=0)
    limit: Mapped[int] = mapped_column(Integer)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    dropped: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Citation(Base):
    __tablename__ = "citation"
    __table_args__ = (UniqueConstraint("catalog_id", "number"),
                      UniqueConstraint("catalog_id", "source_id", "span_ref"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    catalog_id: Mapped[str] = mapped_column(ForeignKey("citation_catalog.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[str] = mapped_column(ID, index=True)
    number: Mapped[int] = mapped_column(Integer)
    source_id: Mapped[str] = mapped_column(ID)
    doc_ref: Mapped[str] = mapped_column(String(300))
    span_ref: Mapped[str] = mapped_column(String(300))
    title: Mapped[str] = mapped_column(String(500))
    text: Mapped[str] = mapped_column(Text)
    locator: Mapped[dict | None] = mapped_column(JSONB)  # models.Locator，带类型（迁移 0018 起）
    locators: Mapped[list | None] = mapped_column(JSONB)  # 片段里每个版面块的 models.Locator（迁移 0018）
    open_url: Mapped[str | None] = mapped_column(String(1000))
    images: Mapped[list | None] = mapped_column(JSONB)  # models.EvidenceImage 的列表
    version_note: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


KNOWLEDGE_TABLES = tuple(
    model.__table__ for model in (KnowledgeSourceRow, CitationCatalogRow, Citation)
)
KNOWLEDGE_RLS_TABLES = ("knowledge_source", "citation_catalog", "citation")
