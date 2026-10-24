"""通用后台任务的表。都带 workspace_id，开启行级安全。

- job：一条任务。状态 queued → running → succeeded | failed | canceled；running 的租约过期后可被别的 worker 重新领取。
  同一对象（kind + subject_id）排队中的最多一条，后来的合并进去（部分唯一索引 uq_job_queued_subject）；
  同一对象有同类任务在运行时，排队的那条不会被领取，所以同一对象的同类任务总是串行。
  唯一索引只管 queued：运行中再来一次请求（例如重新解析）要能排在后面，不能被合并进正在跑的那次。
- version 是载荷格式的版本：滚动升级时旧 worker 只领自己认得的版本，新版本的任务留在队列里等新 worker。
- job_stage：一次执行（try_no 是第几次）里各阶段的起止与结果，界面上的处理时间线读它。
- 轮次（重新解析时作废旧任务）属于业务，放在载荷里，由业务的处理函数核对，任务表不管。
- canceled_by：系统管理员在运行面板取消时记下是谁。业务自己取消（例如删文档时停解析）时业务知道后续怎么办，
  面板上取消的业务并不知道，所以要走业务的失败收尾（on_give_up），文档、附件才不会一直停在「处理中」。
- job_worker：worker 的心跳（运行面板看有几个 worker、各池几个执行槽、在跑几个、下游闸门占用）。
  不分工作空间、不开行级安全；正常停机时删掉自己那行，被强杀的过一天由别的 worker 清掉。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base

ID = String(64)

QUEUED, RUNNING, SUCCEEDED, FAILED, CANCELED = "queued", "running", "succeeded", "failed", "canceled"


class Job(Base):
    __tablename__ = "job"
    __table_args__ = (
        Index("uq_job_queued_subject", "workspace_id", "kind", "subject_id", unique=True,
              postgresql_where=text("status = 'queued'")),
        Index("ix_job_claim", "pool", "status", "next_attempt_at"),
        Index("ix_job_subject", "kind", "subject_id"),
    )

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ID, index=True)
    kind: Mapped[str] = mapped_column(String(64))
    subject_id: Mapped[str] = mapped_column(ID)
    pool: Mapped[str] = mapped_column(String(32))
    priority: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16))
    tries: Mapped[int] = mapped_column(Integer, default=0)
    max_tries: Mapped[int] = mapped_column(Integer)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim_token: Mapped[str | None] = mapped_column(String(64))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stage: Mapped[str | None] = mapped_column(String(64))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    canceled_by: Mapped[str | None] = mapped_column(ID)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_class: Mapped[str | None] = mapped_column(String(16))  # file | service | internal
    error_detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobStage(Base):
    __tablename__ = "job_stage"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ID, index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("job.id", ondelete="CASCADE"), index=True)
    try_no: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))  # running | done | failed | canceled
    error_code: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobWorker(Base):
    __tablename__ = "job_worker"

    id: Mapped[str] = mapped_column(ID, primary_key=True)  # 主机名:进程号:随机串，每次启动换一个
    host: Mapped[str] = mapped_column(String(255))
    pid: Mapped[int] = mapped_column(Integer)
    version: Mapped[str] = mapped_column(String(32))
    pools: Mapped[dict] = mapped_column(JSONB, default=dict)  # 池 → 执行槽数
    busy: Mapped[dict] = mapped_column(JSONB, default=dict)   # 池 → 在跑几个
    kinds: Mapped[list] = mapped_column(JSONB, default=list)  # 注册的任务类型：名字、中文名、池、能否取消
    gates: Mapped[dict] = mapped_column(JSONB, default=dict)  # 闸门 → 占用、上限、是否共用
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


JOB_TABLES = (Job.__table__, JobStage.__table__, JobWorker.__table__)
JOB_RLS_TABLES = ("job", "job_stage")
