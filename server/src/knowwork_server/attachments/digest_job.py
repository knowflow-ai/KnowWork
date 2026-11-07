"""附件导读任务（通用任务框架上的 attachment.digest）：切段 → 各段并发写要点 → 太长就分组压缩 → 全文摘要 → 存导读 →
（已经发出去的）放进会话沙箱。纯函数在 digest.py。

文字放不进对话的长附件（attachments/reading.py 那一档）才做：模型问全文时先读导读，再按导读里的行号读原文，
不用把整份从头读一遍。用户多半在等着（网页发送前会等导读，可以点「直接发送」不等），所以：
- 各段并发（attachment_digest_concurrency，本地模型别打满），每做完一段更新进度，网页显示「生成导读 12/40」；
- 一段失败重试一次，还不行就在导读里标出来、接着做别的段；全部失败才算任务失败（按服务问题重试）；
- 导读失败不影响原件与解析结果，模型照旧按目录读。
"""

import asyncio
import logging
import time
from collections.abc import Sequence
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from ..jobs import Claim, Done, JobContext, JobError, JobFailure, JobKind, JobRunner
from ..models.catalog import DEFAULT_ALIAS
from ..models.client import ModelCallError
from ..sandbox.contract import SandboxError, SandboxTooLarge, SessionSandbox
from ..sessionfiles.storage import SessionStores
from ..skills.errors import BlobIntegrityError
from .digest import (
    COLLAPSE_CHARS,
    FAILED,
    Section,
    collapse_groups,
    collapse_messages,
    overview_messages,
    render,
    section_messages,
    sections_of,
)
from .service import AttachmentService, placement_failure
from .settings import AttachmentSettings

log = logging.getLogger(__name__)

KIND_NAME = "attachment.digest"
PROGRESS_SECONDS = 1.0   # 进度最多每秒写一次库
SECTION_TOKENS = 600
OVERVIEW_TOKENS = 1500


class ChatModel(Protocol):
    async def chat(self, workspace_id: str, alias: str, messages: Sequence[dict[str, Any]], *,
                   max_tokens: int = 1024, temperature: float = 0.2, background: bool = True) -> str: ...


def digest_kind() -> JobKind:
    """入队方（api、worker 的解析任务）与执行方（worker）共用。长文档可能要几百次模型调用，时限放宽到一小时。"""
    return JobKind(KIND_NAME, pool="core", max_tries=3, timeout_seconds=3600, priority=9, label="生成附件导读")


class DigestJob:
    def __init__(self, service: AttachmentService, stores: SessionStores, sandbox: SessionSandbox, models: ChatModel,
                 settings: AttachmentSettings) -> None:
        self.kind = digest_kind()
        self._service = service
        self._stores = stores
        self._sandbox = sandbox
        self._models = models
        self._settings = settings

    def register(self, runner: JobRunner) -> None:
        runner.register(self.kind, self.handle, self.give_up)

    async def handle(self, ctx: JobContext) -> Done | None:
        source = await self._service.digest_source(ctx.workspace_id, ctx.subject_id)
        if source is None:  # 附件已删、不做导读、文字还没有
            return None
        sections = sections_of(source.text)
        summaries = await self._map(ctx, source.filename, sections)
        if all(summary == FAILED for summary in summaries):
            raise JobError("digest_failed", "模型没能生成导读，稍后会重试")
        async with ctx.stage("overview"):
            overview = await self._overview(ctx, source.filename, summaries)
        markdown = render(source.filename, source.text_name, sections, summaries, overview).encode("utf-8")
        async with ctx.stage("store") as stage:
            try:
                stored = await self._stores(ctx.workspace_id, source.scope).put(markdown)
            except BlobIntegrityError as error:
                raise JobError("storage_unavailable", "文件存储暂时不可用") from error
            stage.record(bytes=len(markdown), sections=len(sections))
        placement = await self._service.save_digest(ctx.workspace_id, ctx.subject_id, stored)
        if placement is not None:
            async with ctx.stage("place"):
                try:
                    await self._sandbox.put_input(ctx.workspace_id, placement.conversation_id, placement.parsed_name,
                                                  markdown)
                except SandboxError as error:
                    code, message = placement_failure(error)
                    raise JobError(code, message, error_class="file" if isinstance(error, SandboxTooLarge) else "service",
                                   detail=str(error)) from error
        attachment_id = ctx.subject_id

        async def apply(session: AsyncSession) -> None:
            await self._service.apply_digest(session, attachment_id, status="ready")

        return Done(apply=apply)

    async def _map(self, ctx: JobContext, name: str, sections: list[Section]) -> list[str]:
        """各段并发写要点，按段更新进度。"""
        total, done = len(sections), 0
        last = 0.0
        gate = asyncio.Semaphore(self._settings.attachment_digest_concurrency)
        await self._service.digest_progress(ctx.workspace_id, ctx.subject_id, 0, total)

        async def one(section: Section) -> str:
            nonlocal done, last
            async with gate:
                summary = await self._ask(ctx.workspace_id, section_messages(name, section, total), SECTION_TOKENS)
            done += 1
            if done == total or time.monotonic() - last >= PROGRESS_SECONDS:
                last = time.monotonic()
                await self._service.digest_progress(ctx.workspace_id, ctx.subject_id, done, total)
            return summary

        async with ctx.stage("sections") as stage:
            summaries = list(await asyncio.gather(*(one(section) for section in sections)))
            stage.record(sections=total, failed=summaries.count(FAILED))
        return summaries

    async def _overview(self, ctx: JobContext, name: str, summaries: list[str]) -> str:
        """各段要点合计太长就分组压缩（可能压几轮），再写全文摘要。"""
        material = [summary for summary in summaries if summary != FAILED]
        while len(material) > 1 and sum(map(len, material)) > COLLAPSE_CHARS:
            groups = collapse_groups(material, limit=COLLAPSE_CHARS)
            if len(groups) == len(material):  # 每段都已经很长，压不动了
                break
            material = list(await asyncio.gather(*(
                self._ask(ctx.workspace_id, collapse_messages(name, group), OVERVIEW_TOKENS) for group in groups)))
            material = [summary for summary in material if summary != FAILED] or material
        overview = await self._ask(ctx.workspace_id, overview_messages(name, material), OVERVIEW_TOKENS)
        return "（没能生成全文摘要，请看下面各段要点）" if overview == FAILED else overview

    async def _ask(self, workspace_id: str, messages: list[dict], max_tokens: int) -> str:
        """一次模型调用，失败重试一次；还不行返回 FAILED（这一段在导读里标出来）。用户在等着，用前台的并发额度。"""
        for attempt in (1, 2):
            try:
                text = await self._models.chat(workspace_id, DEFAULT_ALIAS, messages, max_tokens=max_tokens,
                                               temperature=0.2, background=False)
            except ModelCallError as error:
                log.warning("attachment digest call failed (attempt %s): %s", attempt, error.code)
                if not error.retryable:
                    break
                continue
            if text.strip():
                return text.strip()
        return FAILED

    async def give_up(self, session: AsyncSession, claim: Claim, failure: JobFailure) -> None:
        await self._service.apply_digest(session, claim.subject_id, status="failed")
