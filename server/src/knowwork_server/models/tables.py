"""模型接入与用量的表。全部带 workspace_id，供行级安全按空间隔离。

- model_provider：厂商与 API 地址，密钥只存 AES-256-GCM 密文与末四位；
- model：厂商下的一个模型，alias 是宿主请求里写的名字，name 是厂商侧的模型名；
- usage_record：每次模型调用一条（来自宿主事件流），不存提示词与回答原文；
- usage_daily：按天、用户、模型汇总，管理页直接读它。
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base

ID = String(64)
PRICE = Numeric(14, 6)
COST = Numeric(18, 6)


class ModelProvider(Base):
    __tablename__ = "model_provider"
    __table_args__ = (UniqueConstraint("workspace_id", "name", name="uq_model_provider_name"),)

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ID, index=True)
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(32))  # 厂商类型，例如 deepseek、qwen、openai_compatible
    base_url: Mapped[str] = mapped_column(String(500))
    api_key_ciphertext: Mapped[str | None] = mapped_column(Text)
    api_key_last4: Mapped[str | None] = mapped_column(String(4))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


class Model(Base):
    __tablename__ = "model"
    __table_args__ = (UniqueConstraint("workspace_id", "alias", name="uq_model_alias"),)

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ID, index=True)
    provider_id: Mapped[str] = mapped_column(ForeignKey("model_provider.id", ondelete="RESTRICT"), index=True)
    alias: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))
    capabilities: Mapped[list[str]] = mapped_column(ARRAY(String(16)))  # chat | embedding | rerank | vision
    context_length: Mapped[int | None] = mapped_column(Integer)
    # 对话模型单次最多输出多少 token；网关转发时把 max_tokens 收紧到它（宿主按全局默认 32768 请求，有的模型上限更小）
    max_output_tokens: Mapped[int | None] = mapped_column(Integer)
    # 对话模型的思考：on、off，空是跟随服务默认（不补参数）；转发时按服务商的写法补（models/thinking.py）
    thinking: Mapped[str | None] = mapped_column(String(8))
    input_price_per_mtok: Mapped[Decimal | None] = mapped_column(PRICE)
    output_price_per_mtok: Mapped[Decimal | None] = mapped_column(PRICE)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


class UsageRecord(Base):
    __tablename__ = "usage_record"
    # 同一会话同一步只有一次模型调用；宿主重放事件时靠它去重
    __table_args__ = (UniqueConstraint("workspace_id", "conversation_id", "turn", "step", name="uq_usage_step"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(ID, index=True)
    user_id: Mapped[str] = mapped_column(ID, index=True)
    conversation_id: Mapped[str] = mapped_column(String(128), index=True)
    turn: Mapped[int] = mapped_column(Integer)
    step: Mapped[int] = mapped_column(Integer)
    model_alias: Mapped[str] = mapped_column(String(64))
    model_id: Mapped[str | None] = mapped_column(ID)
    input_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    total_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    reasoning_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    cost: Mapped[Decimal | None] = mapped_column(COST)
    status: Mapped[str] = mapped_column(String(16))  # ok | interrupted
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    day: Mapped[date] = mapped_column(Date, index=True)


class UsageDaily(Base):
    __tablename__ = "usage_daily"

    workspace_id: Mapped[str] = mapped_column(ID, primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    user_id: Mapped[str] = mapped_column(ID, primary_key=True)
    model_alias: Mapped[str] = mapped_column(String(64), primary_key=True)
    calls: Mapped[int] = mapped_column(BigInteger, default=0)
    input_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    total_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cost: Mapped[Decimal] = mapped_column(COST, default=Decimal(0))


MODELS_TABLES = tuple(t.__table__ for t in (ModelProvider, Model, UsageRecord, UsageDaily))
MODELS_RLS_TABLES = ("model_provider", "model", "usage_record", "usage_daily")
