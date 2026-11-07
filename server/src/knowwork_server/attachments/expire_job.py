"""一直没发出去的附件到期清理（通用任务框架上的 attachment.expire）。

输入框里选了文件就传上来暂存（service.stage），用户没发就走了，它不该一直占着对象存储。暂存时就排上一条延后的
清理任务（attachment_staged_ttl_hours，默认 24 小时）；到时候还没认领进会话的连文件一起删，已经发出去的不动
（随会话删除时清理）。删到一半停下会留下孤儿文件，所以不可取消；重复执行是空操作。
"""

from ..jobs import Done, JobContext, JobKind, JobRunner
from .service import AttachmentService

KIND_NAME = "attachment.expire"


def expire_kind() -> JobKind:
    """入队方（api 进程）与执行方（worker 进程）共用。"""
    return JobKind(KIND_NAME, pool="core", max_tries=5, cancelable=False, label="清理没发出去的附件")


class ExpireJob:
    def __init__(self, service: AttachmentService) -> None:
        self.kind = expire_kind()
        self._service = service

    def register(self, runner: JobRunner) -> None:
        runner.register(self.kind, self.handle)

    async def handle(self, ctx: JobContext) -> Done | None:
        async with ctx.stage("expire") as stage:
            stage.record(removed=await self._service.expire(ctx.workspace_id, ctx.subject_id))
        return None
