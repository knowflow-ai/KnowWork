"""附件服务：上传校验、存原件、放进沙箱或排队解析、列表、沙箱重建后重放、会话删除时清理。

解析任务排进通用任务表（jobs，类型 attachment.parse），和附件行同一事务写入；执行在 worker 进程（parse_job.py）。

每个事务开头都 set_workspace，普通账号连库时行级安全按工作空间放行；
同一会话的入库按会话加事务级 advisory 锁串行，沙箱里的文件名因此不会撞。
"""

import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..access.rls import set_workspace
from ..conversations.models import Conversation
from ..jobs import JobKind, cancel, enqueue
from ..sandbox.contract import SandboxError, SandboxTooLarge, SessionSandbox
from ..sessionfiles.names import InvalidFileName, clean_name, replace_extension, unique_name
from ..sessionfiles.storage import SessionStores
from ..skills.errors import BlobIntegrityError
from .models import Attachment
from .settings import AttachmentSettings
from .types import PARSED_KINDS, AttachmentRejected, Kind, detect

log = logging.getLogger(__name__)

PARSER_NOT_CONFIGURED = ("parser_not_configured", "未配置文档解析服务")


@dataclass(frozen=True)
class IncomingFile:
    filename: str | None
    data: bytes
    too_large: bool = False  # 读取时已超过上限，data 只是前缀


@dataclass(frozen=True)
class FileFailure:
    filename: str
    code: str
    message: str


@dataclass(frozen=True)
class AttachmentView:
    id: str
    filename: str
    input_name: str
    kind: str
    size: int
    status: str
    error_code: str | None
    error_message: str | None
    created_at: datetime | None
    digest: str = ""
    parsed_name: str | None = None
    parse_status: str | None = None


@dataclass(frozen=True)
class ParseSource:
    """解析任务要用的附件信息。"""

    conversation_id: str
    filename: str
    parsed_name: str
    mime: str
    digest: str
    parsed_digest: str | None  # 上一次已经解析出、存下的 Markdown；放进沙箱失败重试时不用再解析


@dataclass(frozen=True)
class UploadResult:
    items: tuple[AttachmentView, ...]
    failures: tuple[FileFailure, ...]


def view(row: Attachment) -> AttachmentView:
    return AttachmentView(id=row.id, filename=row.filename, input_name=row.input_name, kind=row.kind, size=row.size,
                          status=row.status, error_code=row.error_code, error_message=row.error_message,
                          created_at=row.created_at, digest=row.digest, parsed_name=row.parsed_name,
                          parse_status=row.parse_status)


def placement_failure(error: SandboxError) -> tuple[str, str]:
    if isinstance(error, SandboxTooLarge):
        return "sandbox_too_large", str(error) or "文件超过沙箱上限"
    return "sandbox_unavailable", str(error) or "沙箱暂时不可用"


async def lock_session(session: AsyncSession, conversation_id: str) -> None:
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                          {"key": f"attachment:{conversation_id}"})


class AttachmentService:
    def __init__(self, sessionmaker: async_sessionmaker, stores: SessionStores, sandbox: SessionSandbox,
                 settings: AttachmentSettings, parse_kind: JobKind, *,
                 parser_configured: Callable[[str], Awaitable[bool]]) -> None:
        self._sessionmaker = sessionmaker
        self._stores = stores
        self._sandbox = sandbox
        self._settings = settings
        self._parse_kind = parse_kind
        # 这个空间配没配文档解析服务（管理后台里能随时改，所以每次上传时问）
        self._parser_configured = parser_configured

    @asynccontextmanager
    async def _tx(self, workspace_id: str) -> AsyncIterator[AsyncSession]:
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            yield session

    @property
    def max_bytes(self) -> int:
        return self._settings.attachment_max_bytes

    @property
    def max_files(self) -> int:
        return self._settings.attachment_max_files_per_request

    async def upload(self, conversation: Conversation, files: Sequence[IncomingFile]) -> UploadResult:
        items: list[AttachmentView] = []
        failures: list[FileFailure] = []
        for incoming in files:
            try:
                row = await self._accept(conversation, incoming)
            except AttachmentRejected as error:
                failures.append(FileFailure(_display(incoming.filename), error.code, error.message))
                continue
            placed, failure = await self._place_original(conversation, row, incoming.data)
            items.append(placed)
            if failure is not None:
                failures.append(failure)
        return UploadResult(items=tuple(items), failures=tuple(failures))

    def _validate(self, incoming: IncomingFile) -> tuple[str, Kind, str]:
        limit_mib = self.max_bytes // (1024 * 1024)
        if incoming.too_large or len(incoming.data) > self.max_bytes:
            raise AttachmentRejected("too_large", f"文件超过 {limit_mib} MiB 上限")
        try:
            name = clean_name(incoming.filename)
        except InvalidFileName as error:
            raise AttachmentRejected("invalid_name", str(error)) from error
        detected = detect(name, incoming.data)
        return name, detected.kind, detected.mime

    async def _accept(self, conversation: Conversation, incoming: IncomingFile) -> Attachment:
        name, kind, mime = self._validate(incoming)
        try:
            digest = await self._stores(conversation.workspace_id, conversation.id).put(incoming.data)
        except BlobIntegrityError as error:
            log.warning("attachment store failed: %s", type(error).__name__)
            raise AttachmentRejected("storage_unavailable", "文件存储暂时不可用，请稍后重试") from error
        # 原件总是原样放进沙箱（照官方：文件按原字节保存，模型用文件工具按需读）；配了 MinerU 的 PDF、Word、PPT
        # 另外解析出一份同名 Markdown 放在旁边，方便读长文档。没配 MinerU 不影响上传，沙箱里有读 Office 文件的工具。
        parsed = kind in PARSED_KINDS and await self._parser_configured(conversation.workspace_id)
        async with self._tx(conversation.workspace_id) as session:
            await lock_session(session, conversation.id)
            names = (await session.execute(select(Attachment.input_name, Attachment.parsed_name)
                                           .where(Attachment.conversation_id == conversation.id))).all()
            taken = [n for pair in names for n in pair if n]
            input_name = unique_name(name, taken)
            parsed_name = unique_name(replace_extension(name, "md"), [*taken, input_name]) if parsed else None
            row = Attachment(id=f"att_{uuid.uuid4().hex}", workspace_id=conversation.workspace_id,
                             conversation_id=conversation.id, user_id=conversation.user_id, filename=name,
                             input_name=input_name, parsed_name=parsed_name, kind=kind.value, mime=mime,
                             size=len(incoming.data), digest=digest, status="placing",
                             parse_status="parsing" if parsed else None, created_at=datetime.now(UTC))
            session.add(row)
            if parsed:
                await enqueue(session, self._parse_kind, workspace_id=conversation.workspace_id, subject_id=row.id)
        return row

    async def _place_original(self, conversation: Conversation, row: Attachment,
                              data: bytes) -> tuple[AttachmentView, FileFailure | None]:
        try:
            await self._sandbox.put_input(conversation.workspace_id, conversation.id, row.input_name, data)
        except SandboxError as error:
            code, message = placement_failure(error)
            await self.mark(conversation.workspace_id, row.id, status="place_failed", code=code, message=message)
            failed = replace(view(row), status="place_failed", error_code=code, error_message=message)
            return failed, FileFailure(row.filename, code, message)
        await self.mark(conversation.workspace_id, row.id, status="ready")
        return replace(view(row), status="ready"), None

    async def parse_source(self, workspace_id: str, attachment_id: str) -> ParseSource | None:
        """解析任务开始时读附件；附件已删（会话删了）或不需要解析时返回 None。"""
        async with self._tx(workspace_id) as session:
            row = await session.get(Attachment, attachment_id)
        if row is None or not row.parsed_name:
            return None
        return ParseSource(conversation_id=row.conversation_id, filename=row.filename, parsed_name=row.parsed_name,
                           mime=row.mime, digest=row.digest, parsed_digest=row.parsed_digest)

    async def save_parsed(self, workspace_id: str, attachment_id: str, parsed_digest: str) -> None:
        """解析结果一存下就记到附件上：之后放进沙箱失败重试、或沙箱重建时重放，都不用再调解析服务。"""
        async with self._tx(workspace_id) as session:
            await session.execute(update(Attachment).where(Attachment.id == attachment_id)
                                  .values(parsed_digest=parsed_digest, updated_at=func.now()))

    async def apply_parse(self, session: AsyncSession, attachment_id: str, *, parse_status: str,
                          code: str | None = None, message: str | None = None,
                          parsed_digest: str | None = None) -> None:
        """写解析结果，在任务收尾的事务里调用。只动 parse_status，不动原件的 status：解析失败时原件照样可用。"""
        values = {"parse_status": parse_status, "updated_at": func.now()}
        if code is not None:
            values.update(error_code=code, error_message=message and message[:500])
        if parsed_digest is not None:
            values["parsed_digest"] = parsed_digest
        await session.execute(update(Attachment).where(Attachment.id == attachment_id).values(**values))

    async def ready(self, conversation: Conversation, ids: Sequence[str]) -> tuple[AttachmentView, ...]:
        """本轮消息带的附件：必须属于这个会话、原件已放进沙箱。缺一个就整轮拒绝，免得模型以为有文件其实没有。"""
        wanted = list(dict.fromkeys(ids))
        if not wanted:
            return ()
        async with self._tx(conversation.workspace_id) as session:
            rows = {row.id: row for row in (await session.scalars(select(Attachment).where(
                Attachment.conversation_id == conversation.id, Attachment.id.in_(wanted)))).all()}
        missing = [i for i in wanted if i not in rows or rows[i].status != "ready"]
        if missing:
            raise AttachmentRejected("attachment_unavailable", "有附件还没准备好或已失效，请移除后重新上传")
        return tuple(view(rows[i]) for i in wanted)

    async def mark(self, workspace_id: str, attachment_id: str, *, status: str, code: str | None = None,
                   message: str | None = None, parsed_digest: str | None = None) -> None:
        values = {"status": status, "error_code": code, "error_message": message and message[:500],
                  "updated_at": func.now()}
        if parsed_digest is not None:
            values["parsed_digest"] = parsed_digest
        async with self._tx(workspace_id) as session:
            await session.execute(update(Attachment).where(Attachment.id == attachment_id).values(**values))

    async def list(self, conversation: Conversation) -> tuple[AttachmentView, ...]:
        async with self._tx(conversation.workspace_id) as session:
            rows = (await session.scalars(select(Attachment).where(Attachment.conversation_id == conversation.id)
                                          .order_by(Attachment.created_at, Attachment.id))).all()
        return tuple(view(row) for row in rows)

    async def restage(self, workspace_id: str, session_id: str) -> tuple[FileFailure, ...]:
        """沙箱重建（容器回收、工作区丢失）后把已就绪的附件重新放进去；放不进的逐个返回。"""
        async with self._tx(workspace_id) as session:
            rows = (await session.scalars(select(Attachment).where(
                Attachment.conversation_id == session_id,
                Attachment.status.in_(("ready", "place_failed"))))).all()
        failures = []
        for row in rows:
            failure = await self._restage_one(workspace_id, session_id, row)
            if failure is not None:
                failures.append(failure)
        return tuple(failures)

    async def _restage_one(self, workspace_id: str, session_id: str, row: Attachment) -> FileFailure | None:
        # 迁移 0013 之前的行：PDF、Word、PPT 只放了 Markdown（parsed_name 等于 input_name），照旧只放它
        legacy = row.parsed_name is not None and row.parsed_name == row.input_name
        copies = [] if legacy else [(row.input_name, row.digest, self.max_bytes)]
        if row.parsed_name and row.parsed_digest:
            copies.append((row.parsed_name, row.parsed_digest, self.max_bytes * 4))  # Markdown 可能比原件大（表格展开）
        store = self._stores(workspace_id, session_id)
        try:
            for name, digest, cap in copies:
                await self._sandbox.put_input(workspace_id, session_id, name, await store.get(digest, max_bytes=cap))
        except BlobIntegrityError:
            return FileFailure(row.filename, "content_missing", "附件内容已不可用，请重新上传")
        except SandboxError as error:
            code, message = placement_failure(error)
            await self.mark(workspace_id, row.id, status="place_failed", code=code, message=message)
            return FileFailure(row.filename, code, message)
        if row.status != "ready":
            await self.mark(workspace_id, row.id, status="ready")
        return None

    async def erase(self, conversation: Conversation) -> None:
        """会话删除时调用：取消解析任务、删行，再删该会话命名空间里的原件与解析结果。

        运行中的解析任务收到取消标记后停下；万一它已经在收尾，写结果时附件行已不在，只是空操作。
        """
        async with self._tx(conversation.workspace_id) as session:
            rows = (await session.execute(select(Attachment.id, Attachment.digest, Attachment.parsed_digest)
                                          .where(Attachment.conversation_id == conversation.id))).all()
            await cancel(session, self._parse_kind, [row.id for row in rows])
            await session.execute(delete(Attachment).where(Attachment.conversation_id == conversation.id))
        store = self._stores(conversation.workspace_id, conversation.id)
        for digest in {d for row in rows for d in (row.digest, row.parsed_digest) if d}:
            try:
                await store.delete(digest)
            except BlobIntegrityError:
                log.warning("failed to delete attachment object of %s", conversation.id)


def _display(filename: str | None) -> str:
    return (filename or "")[-200:] or "（未命名文件）"
