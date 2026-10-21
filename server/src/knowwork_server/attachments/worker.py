"""附件解析 worker：领取一个任务、调 MinerU、把 Markdown 放进沙箱。

- 领取：一个短事务里 SELECT ... FOR UPDATE SKIP LOCKED 取一条可执行的任务（排队到点的，或租约已过期的
  运行中任务——说明上一个 worker 中断了），写上 claim_token、租约与尝试次数后立即提交；
- 执行：不持有事务调 MinerU（可能很久）；
- 交回：只在 claim_token 仍是自己的时候写结果，防止租约过期后被别人重领又被自己覆盖。

失败分类：格式不支持、文件损坏、解析服务版本不支持 → 不重试；服务超时、服务暂不可用 → 重试到 max_attempts。
"""

import asyncio
import logging
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..access.rls import set_workspace
from ..sandbox.contract import SandboxError, SessionSandbox
from ..sessionfiles.storage import SessionStores
from ..skills.errors import BlobIntegrityError
from .mineru import MinerUClient, MinerUError
from .models import Attachment, AttachmentParseJob
from .service import PARSER_NOT_CONFIGURED, AttachmentService, placement_failure
from .settings import AttachmentSettings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Claim:
    job_id: str
    token: str
    attempts: int
    max_attempts: int
    attachment_id: str
    conversation_id: str
    filename: str
    parsed_name: str
    mime: str
    digest: str


@dataclass(frozen=True)
class Outcome:
    status: str  # succeeded | retry | failed
    error_code: str | None = None


def _now() -> datetime:
    return datetime.now(UTC)


class ParseWorker:
    def __init__(self, sessionmaker: async_sessionmaker, stores: SessionStores, sandbox: SessionSandbox,
                 attachments: AttachmentService, mineru: MinerUClient | None, settings: AttachmentSettings, *,
                 clock: Callable[[], datetime] = _now) -> None:
        self._sessionmaker = sessionmaker
        self._stores = stores
        self._sandbox = sandbox
        self._attachments = attachments
        self._mineru = mineru
        self._settings = settings
        self._clock = clock

    async def run_one(self, workspace_id: str) -> Outcome | None:
        """领取并执行一个任务；没有可执行的任务返回 None。"""
        claim = await self.claim(workspace_id)
        if claim is None:
            return None
        if claim.attempts > claim.max_attempts:  # 多次领取后都中断，不再无休止地重来
            return await self._fail(workspace_id, claim, "interrupted", "文档解析多次中断，请重新上传")
        return await self._execute(workspace_id, claim)

    async def claim(self, workspace_id: str) -> Claim | None:
        now = self._clock()
        ready = or_(and_(AttachmentParseJob.status == "queued", AttachmentParseJob.next_attempt_at <= now),
                    and_(AttachmentParseJob.status == "running", AttachmentParseJob.lease_until < now))
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            job = await session.scalar(select(AttachmentParseJob).where(ready)
                                       .order_by(AttachmentParseJob.next_attempt_at, AttachmentParseJob.id)
                                       .limit(1).with_for_update(skip_locked=True))
            if job is None:
                return None
            attachment = await session.get(Attachment, job.attachment_id)
            if attachment is None or not attachment.parsed_name:  # 附件刚被删（任务随外键级联），或不需要解析
                return None
            token, attempts = uuid.uuid4().hex, job.attempts + 1
            lease = now + timedelta(seconds=self._settings.attachment_parse_lease_seconds)
            await session.execute(update(AttachmentParseJob).where(AttachmentParseJob.id == job.id).values(
                status="running", attempts=attempts, claim_token=token, lease_until=lease, updated_at=now))
            return Claim(job_id=job.id, token=token, attempts=attempts, max_attempts=job.max_attempts,
                         attachment_id=attachment.id, conversation_id=attachment.conversation_id,
                         filename=attachment.filename, parsed_name=attachment.parsed_name, mime=attachment.mime,
                         digest=attachment.digest)

    async def _execute(self, workspace_id: str, claim: Claim) -> Outcome:
        if self._mineru is None:
            return await self._fail(workspace_id, claim, *PARSER_NOT_CONFIGURED)
        store = self._stores(workspace_id, claim.conversation_id)
        try:
            original = await store.get(claim.digest, max_bytes=self._settings.attachment_max_bytes)
            markdown = (await self._mineru.parse(claim.filename, original, claim.mime)).encode("utf-8")
            parsed_digest = await store.put(markdown)
        except BlobIntegrityError:
            return await self._fail(workspace_id, claim, "content_missing", "附件内容已不可用，请重新上传")
        except MinerUError as error:
            if error.retryable and claim.attempts < claim.max_attempts:
                return await self._retry(workspace_id, claim, error.code)
            return await self._fail(workspace_id, claim, error.code, error.message)
        return await self._place(workspace_id, claim, markdown, parsed_digest)

    async def _place(self, workspace_id: str, claim: Claim, markdown: bytes, parsed_digest: str) -> Outcome:
        try:
            await self._sandbox.put_input(workspace_id, claim.conversation_id, claim.parsed_name, markdown)
        except SandboxError as error:
            code, message = placement_failure(error)
            if await self._finish(workspace_id, claim, "succeeded", None):
                await self._attachments.mark_parse(workspace_id, claim.attachment_id, parse_status="failed",
                                                   code=code, message=message, parsed_digest=parsed_digest)
            return Outcome("succeeded", code)
        if await self._finish(workspace_id, claim, "succeeded", None):
            await self._attachments.mark_parse(workspace_id, claim.attachment_id, parse_status="ready",
                                               parsed_digest=parsed_digest)
        return Outcome("succeeded")

    async def _retry(self, workspace_id: str, claim: Claim, code: str) -> Outcome:
        delay = self._settings.attachment_parse_retry_delay_seconds * claim.attempts
        await self._finish(workspace_id, claim, "queued", code,
                           next_attempt_at=self._clock() + timedelta(seconds=delay))
        return Outcome("retry", code)

    async def _fail(self, workspace_id: str, claim: Claim, code: str, message: str) -> Outcome:
        if await self._finish(workspace_id, claim, "failed", code):
            await self._attachments.mark_parse(workspace_id, claim.attachment_id, parse_status="failed", code=code,
                                               message=message)
        return Outcome("failed", code)

    async def _finish(self, workspace_id: str, claim: Claim, status: str, code: str | None, *,
                      next_attempt_at: datetime | None = None) -> bool:
        values = {"status": status, "last_error_code": code, "claim_token": None, "lease_until": None,
                  "updated_at": self._clock()}
        if next_attempt_at is not None:
            values["next_attempt_at"] = next_attempt_at
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            result = await session.execute(update(AttachmentParseJob).where(
                AttachmentParseJob.id == claim.job_id, AttachmentParseJob.claim_token == claim.token).values(**values))
        if result.rowcount != 1:
            log.warning("parse job %s was reclaimed before it finished", claim.job_id)
        return result.rowcount == 1

    async def run_forever(self, workspace_ids: Callable[[], Iterable[str]], stop: asyncio.Event, *,
                          idle_seconds: float = 2.0) -> None:
        """worker 进程的主循环：各工作空间轮流取任务，都没活时等 idle_seconds；单个任务出错不让循环退出。"""
        while not stop.is_set():
            busy = False
            for workspace_id in workspace_ids():
                try:
                    busy = await self.run_one(workspace_id) is not None or busy
                except Exception:
                    log.exception("attachment parse job crashed in workspace %s", workspace_id)
            if not busy:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=idle_seconds)
                except TimeoutError:
                    pass
