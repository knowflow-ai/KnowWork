"""知识模块的表。都带 workspace_id，开启行级安全（KNOWLEDGE_RLS_TABLES）。

密钥字段只存 AES-256-GCM 密文与末四位；明文只在调用知识源的那一刻在内存里出现。
user_id 暂不加外键，与 access 模块一致，整合时统一补。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
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




class KnowledgeSearchLog(Base):
    """一次知识检索：聊天里每调一次 knowledge_search 记一条（知识库运营看查了什么、命中什么、用没用上）。
    turn 是第几轮，检索时不知道的在回合结束时补上（search_log）。对外 REST、MCP 的检索没有会话，不记。
    和引用目录一样不对会话表建外键，删会话时由清理函数一起删。"""

    __tablename__ = "knowledge_search_log"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ID, index=True)
    conversation_id: Mapped[str] = mapped_column(ID, index=True)
    turn: Mapped[int | None] = mapped_column(Integer)
    user_id: Mapped[str] = mapped_column(ID)
    query: Mapped[str] = mapped_column(String(1000))
    options: Mapped[dict] = mapped_column(JSONB, default=dict)  # 检索方式、查了哪些库、文件夹、标签、元数据条件
    # 这次检索是哪次工具调用（会话日志里的 callId），运行详情据此把检索过程对上那一步；之前的记录为空（迁移 0028）
    call_id: Mapped[str | None] = mapped_column(String(200))
    # 检索过程：各知识源交回的各路召回、重排的情况（SearchTraceEntry 的列表），只给运行详情看，不给模型
    trace: Mapped[list | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class KnowledgeSearchHit(Base):
    """一次检索的一条命中（按名次）：number 是它在会话引用目录里的编号，回答里用到这个编号就标成被引用。"""

    __tablename__ = "knowledge_search_hit"

    log_id: Mapped[str] = mapped_column(ForeignKey("knowledge_search_log.id", ondelete="CASCADE"), primary_key=True)
    rank: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ID, index=True)
    number: Mapped[int | None] = mapped_column(Integer)
    source_id: Mapped[str] = mapped_column(ID)
    kb_id: Mapped[str] = mapped_column(ID, index=True)  # 知识源内的知识库 id
    doc_ref: Mapped[str] = mapped_column(String(500))
    span_ref: Mapped[str] = mapped_column(String(500))
    title: Mapped[str] = mapped_column(String(500))
    score: Mapped[float | None] = mapped_column(Float)
    cited: Mapped[bool] = mapped_column(Boolean, default=False)


class KnowledgeAnswerCheck(Base):
    """等着判断的回合：检索过知识库、正常答完的一轮，记下问题和最终回答，后台每小时一批交给模型判断答上了没有
    （answer_check）。判完就删，结论写进每轮汇总的知识事实。和检索记录一样不对会话表建外键，删会话时一起删。"""

    __tablename__ = "knowledge_answer_check"
    __table_args__ = (UniqueConstraint("conversation_id", "turn"),)

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ID, index=True)
    conversation_id: Mapped[str] = mapped_column(ID)
    turn: Mapped[int] = mapped_column(Integer)
    question: Mapped[str] = mapped_column(String(2000))
    answer: Mapped[str] = mapped_column(Text)
    tries: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # 判过几次都没拿到结论
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


KNOWLEDGE_RLS_TABLES = ("knowledge_source", "citation_catalog", "citation", "knowledge_search_log",
                        "knowledge_search_hit", "knowledge_answer_check")
KNOWLEDGE_TABLES = tuple(
    model.__table__ for model in (KnowledgeSourceRow, CitationCatalogRow, Citation, KnowledgeSearchLog,
                                  KnowledgeSearchHit, KnowledgeAnswerCheck)
)
