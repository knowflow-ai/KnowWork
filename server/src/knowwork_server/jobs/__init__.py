"""通用后台任务：PostgreSQL 任务表做队列，入队与业务数据同一事务。

装配：
    KIND = JobKind("attachment.parse", pool="core", max_tries=3)          # 入队方与执行方共用一份定义
    await enqueue(session, KIND, workspace_id=..., subject_id=...)         # 业务事务里
    runner = JobRunner(sessionmaker, pools={"core": 2})                    # worker 进程
    runner.register(KIND, handler, on_give_up, on_retry)                 # on_retry 可选：失败后等重试时调
    await runner.run_forever(lambda: ["default"], stop)
处理函数：async def handler(ctx: JobContext) -> Done | None，分阶段写在 `async with ctx.stage("parse"):` 里；
失败抛 JobError 并标明类别（file / service / internal），其余异常按内部错误处理。
"""

from .context import JobContext, Stage
from .kinds import JobCanceled, JobError, JobFailure, JobKind, JobLost, JobReleased
from .listen import listen
from .models import JOB_RLS_TABLES, JOB_TABLES, Job, JobStage
from .queue import cancel, enqueue
from .runner import JobRunner, Outcome
from .store import Claim, Done, OnRetry

__all__ = [
    "JOB_RLS_TABLES",
    "JOB_TABLES",
    "Claim",
    "Done",
    "Job",
    "JobCanceled",
    "JobContext",
    "JobError",
    "JobFailure",
    "JobKind",
    "JobLost",
    "JobReleased",
    "JobRunner",
    "JobStage",
    "OnRetry",
    "Outcome",
    "Stage",
    "cancel",
    "enqueue",
    "listen",
]
