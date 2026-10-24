"""附件解析任务（通用任务框架上的 attachment.parse）：取原件 → MinerU 解析 → 存 Markdown → 放进沙箱。

领取、续租、重试、超时、停机都由任务框架负责；这里只管四个阶段和失败分类：
- 附件已删（会话删了）：什么都不做，任务直接结束；
- 没配置解析服务：不重试；原件内容丢了：文件问题，不重试；
- MinerU 报格式不支持、文件损坏：文件问题，不重试；超时、不可用、任务失败但看不出是文件问题：服务问题，重试；
- 放进沙箱失败（容器正被 api 进程创建、Docker 暂时不可用）：服务问题，重试。解析结果在 store 阶段已经
  记到附件上，重试时直接取，不再调解析服务；重试用尽时 Markdown 也还在，沙箱重建时重放（restage）补上。
  超过沙箱上限的不重试。
最终失败时把附件的 parse_status 标成 failed（和任务收尾同一事务）；原件不受影响，照样可用。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from ..jobs import Claim, Done, JobContext, JobError, JobFailure, JobKind, JobRunner
from ..sandbox.contract import SandboxError, SandboxTooLarge, SessionSandbox
from ..sessionfiles.storage import SessionStores
from ..skills.errors import BlobIntegrityError
from .mineru import MinerUError, MinerUOptions, MinerUProvider
from .service import PARSER_NOT_CONFIGURED, AttachmentService, ParseSource, placement_failure
from .settings import AttachmentSettings

KIND_NAME = "attachment.parse"
# 用户在对话里等着结果，排在批量导入、同步这类任务之前（迁移 0017 搬旧任务时用的也是这个值）
PARSE_PRIORITY = 10
_FILE_CODES = frozenset({"unsupported_format", "file_corrupt"})
# 任务框架自己的失败码，换成附件场景下给用户看的话
_MESSAGES = {
    "interrupted": "文档解析多次中断，请重新上传",
    "job_timeout": "文档解析超时",
    "internal_error": "文档解析出错，请重新上传",
    "finish_failed": "文档解析出错，请重新上传",
}


def parse_kind(settings: AttachmentSettings) -> JobKind:
    """入队方（api 进程）与执行方（worker 进程）共用。重试间隔按次数递增：30 秒、60 秒……"""
    delay = settings.attachment_parse_retry_delay_seconds
    tries = settings.attachment_parse_max_attempts
    return JobKind(KIND_NAME, pool="core", max_tries=tries, lease_seconds=settings.attachment_parse_lease_seconds,
                   timeout_seconds=settings.mineru_parse_timeout_seconds + 300,  # MinerU 自己有时限，这里只兜底
                   retry_delays=tuple(delay * n for n in range(1, max(tries, 2))), priority=PARSE_PRIORITY,
                   label="会话附件解析")


def _job_error(error: MinerUError) -> JobError:
    error_class = "file" if error.code in _FILE_CODES else "service"
    return JobError(error.code, error.message, error_class=error_class, retryable=error.retryable)


class ParseJob:
    def __init__(self, service: AttachmentService, stores: SessionStores, sandbox: SessionSandbox,
                 mineru: MinerUProvider, options: MinerUOptions, settings: AttachmentSettings) -> None:
        self.kind = parse_kind(settings)
        self._service = service
        self._stores = stores
        self._sandbox = sandbox
        self._mineru = mineru
        self._options = options
        self._settings = settings

    def register(self, runner: JobRunner) -> None:
        runner.register(self.kind, self.handle, self.give_up)

    async def handle(self, ctx: JobContext) -> Done | None:
        async with ctx.stage("fetch") as stage:
            source = await self._service.parse_source(ctx.workspace_id, ctx.subject_id)
            if source is None:  # 附件刚被删，或者不需要解析
                return None
            markdown = await self._parsed_before(ctx, source)
            stage.record(reused=markdown is not None)
        if markdown is None:
            markdown = await self._parse(ctx, source)
        async with ctx.stage("place"):
            try:
                await self._sandbox.put_input(ctx.workspace_id, source.conversation_id, source.parsed_name, markdown)
            except SandboxError as error:
                code, message = placement_failure(error)
                raise JobError(code, message, error_class="file" if isinstance(error, SandboxTooLarge) else "service",
                               detail=str(error)) from error
        attachment_id = ctx.subject_id

        async def apply(session: AsyncSession) -> None:
            await self._service.apply_parse(session, attachment_id, parse_status="ready")

        return Done(apply=apply)

    async def _parsed_before(self, ctx: JobContext, source: ParseSource) -> bytes | None:
        if not source.parsed_digest:
            return None
        try:  # Markdown 可能比原件大（表格展开），上限放宽到 4 倍，与 restage 一致
            return await self._stores(ctx.workspace_id, source.conversation_id).get(
                source.parsed_digest, max_bytes=self._settings.attachment_max_bytes * 4)
        except BlobIntegrityError:
            return None  # 存的结果丢了，重新解析

    async def _parse(self, ctx: JobContext, source: ParseSource) -> bytes:
        mineru = await self._mineru.get(ctx.workspace_id, self._options)  # 管理后台改了配置，下一次解析就用新的
        if mineru is None:
            raise JobError(*PARSER_NOT_CONFIGURED, retryable=False)
        store = self._stores(ctx.workspace_id, source.conversation_id)
        async with ctx.stage("parse") as stage:
            try:
                original = await store.get(source.digest, max_bytes=self._settings.attachment_max_bytes)
            except BlobIntegrityError as error:
                raise JobError("content_missing", "附件内容已不可用，请重新上传", error_class="file") from error
            try:
                markdown = (await mineru.parse(source.filename, original, source.mime)).encode("utf-8")
            except MinerUError as error:
                raise _job_error(error) from error
            stage.record(bytes=len(original))
        async with ctx.stage("store") as stage:
            try:
                parsed_digest = await store.put(markdown)
            except BlobIntegrityError as error:
                raise JobError("storage_unavailable", "文件存储暂时不可用") from error
            await self._service.save_parsed(ctx.workspace_id, ctx.subject_id, parsed_digest)
            stage.record(bytes=len(markdown))
        return markdown

    async def give_up(self, session: AsyncSession, claim: Claim, failure: JobFailure) -> None:
        await self._service.apply_parse(session, claim.subject_id, parse_status="failed", code=failure.code,
                                        message=_MESSAGES.get(failure.code, failure.message))
