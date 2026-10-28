"""会话与事件的持久化模型。第一阶段先只有默认工作空间，表结构从一开始就带 workspace_id。"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base


class Conversation(Base):
    __tablename__ = "conversation"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # 最近一次发消息或改名的时间，会话列表按它倒序
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class RunEvent(Base):
    """宿主事件的持久副本，是界面回放的唯一来源。

    - seq 是会话内单调递增的序号，由事件泵在推送前分配（落库是批量异步的），
      主键 (conversation_id, seq) 同时就是回放用的复合索引；
    - data 是宿主原始帧的 data，完整保存；ui 是这一帧投影出的 events.v1 事件（可能为空列表）；
    - kind 为 kernel.invalid 表示这帧存不下或格式不对，只留占位；kernel.gap 表示宿主缓冲已丢帧。
    """

    __tablename__ = "run_event"
    __table_args__ = (UniqueConstraint("conversation_id", "host_epoch", "host_cursor"),)

    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversation.id", ondelete="CASCADE"), primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    workspace_id: Mapped[str] = mapped_column(String(64))
    host_epoch: Mapped[str] = mapped_column(String(64))
    host_cursor: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict | list | None] = mapped_column(JSONB)
    ui: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RunSummary(Base):
    """每一轮的汇总：一轮一行，回合开始时插（进行中），结束时补齐（summaries.RunSummaries 写）。

    管理端「运行记录」的列表与按状态、提问人、有没有引用筛选都读它，不再每次从原始事件帧里现凑；点开一轮的详情
    照旧读原始帧。以后的问答大盘、知识库运营也从这里算。facts 是各模块补的事实（如 {"knowledge": {"searches": 2,
    "cited": 1}}），按模块名分开，回合结束时由各模块给出。"""

    __tablename__ = "run_summary"
    __table_args__ = (Index("ix_run_summary_workspace_started", "workspace_id", "started_at"),)

    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversation.id", ondelete="CASCADE"), primary_key=True)
    turn: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[str] = mapped_column(String(64))
    question: Mapped[str] = mapped_column(String(2000), default="")  # 用户这一轮亲自发的第一条消息
    status: Mapped[str] = mapped_column(String(16))  # running | completed | cancelled | failed
    end_reason: Mapped[str | None] = mapped_column(String(32))  # 宿主给的结束原因原文（completed、aborted、error……）
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tools: Mapped[list] = mapped_column(JSONB, default=list)  # 这一轮调过的工具名，按第一次调用的先后
    facts: Mapped[dict] = mapped_column(JSONB, default=dict)


class AnswerFeedback(Base):
    """对一轮回答的赞或踩（照 DSH 官方的消息反馈 dsh-message-feedback：评分、可选的分类与说明，只记录，不进模型上下文）。
    一轮一条，按会话与 run_id（events.v1 的 run-<轮次>）定位；再点一次已选的评分就撤回（删掉这一条）。
    运行记录按它筛被点踩的轮次。"""

    __tablename__ = "answer_feedback"

    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversation.id", ondelete="CASCADE"), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64))
    rating: Mapped[str] = mapped_column(String(8))           # like | dislike
    category: Mapped[str | None] = mapped_column(String(40))  # DSH 官方的固定分类之一，可以不选
    note: Mapped[str | None] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())
