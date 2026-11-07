"""附件服务：上传校验、存原件、排队解析、发消息时认领进对话并放进沙箱、列表、沙箱重建后重放、删除与过期清理。

文件先有、对话后建（models.py）：stage 传上来先挂在人名下，claim 在发消息时把它们认领进对话；按对话上传的接口
（upload）就是暂存后马上认领，两条路是同一套代码。解析与过期清理排进通用任务表（jobs，attachment.parse /
attachment.expire），和附件行同一事务写入；执行在 worker 进程（parse_job.py、expire_job.py）。

每个事务开头都 set_workspace，普通账号连库时行级安全按工作空间放行；认领进同一会话按会话加事务级 advisory 锁串行，
沙箱里的文件名因此不会撞。解析出的 Markdown 谁放进沙箱靠行锁定：解析任务存结果与认领都锁住附件行，
先存后认领的由认领放，先认领后存的由解析任务放（save_parsed 返回放到哪）。
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
from ..sessionfiles.names import InvalidFileName, clean_name, extension, replace_extension, unique_name
from ..sessionfiles.storage import SessionStores
from ..skills.errors import BlobIntegrityError
from .models import Attachment
from .settings import AttachmentSettings
from .types import EXTRACTED_KINDS, OCR_KINDS, PROSE_EXTENSIONS, AttachmentRejected, Kind, decode_text, detect

log = logging.getLogger(__name__)

STAGED = "staged"
UNAVAILABLE = ("attachment_unavailable", "有附件还没准备好或已失效，请移除后重新上传")
NOT_FOUND = ("attachment_not_found", "附件不存在")
ALREADY_SENT = ("attachment_sent", "已经发出去的附件不能单独删除")
MARKDOWN_FACTOR = 4  # Markdown 可能比原件大（表格展开）


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
    scope: str = ""                  # 对象存储的命名空间（models.py 的 store_scope）
    parsed_digest: str | None = None
    digest_name: str | None = None   # 导读（digest_job.py）
    digest_status: str | None = None
    digest_done: int | None = None
    digest_total: int | None = None
    digest_digest: str | None = None
    page_count: int | None = None    # PDF 的页数（models.py）


@dataclass(frozen=True)
class ParseSource:
    """解析任务要用的附件信息。"""

    filename: str
    kind: str
    mime: str
    digest: str
    scope: str                   # 对象存储的命名空间（models.py 的 store_scope）
    parsed_digest: str | None    # 上一次已经解析出、存下的 Markdown；放进沙箱失败重试时不用再解析


@dataclass(frozen=True)
class Placement:
    """解析出的 Markdown 该放进哪个会话的沙箱、叫什么名字；还没发出去时为 None。"""

    conversation_id: str
    parsed_name: str


@dataclass(frozen=True)
class DigestSource:
    """导读任务要用的：文件名、给模型读的那份文字叫什么（导读里指给模型按行号去读）、文字本身。"""

    filename: str
    text_name: str
    text: str
    scope: str


@dataclass(frozen=True)
class UploadResult:
    items: tuple[AttachmentView, ...]
    failures: tuple[FileFailure, ...]


@dataclass(frozen=True)
class _Claimed:
    view: AttachmentView
    scope: str
    parsed_digest: str | None
    digest_digest: str | None
    fresh: bool  # 这次才认领进来的，要放进沙箱


def view(row: Attachment) -> AttachmentView:
    return AttachmentView(id=row.id, filename=row.filename, input_name=row.input_name, kind=row.kind, size=row.size,
                          status=row.status, error_code=row.error_code, error_message=row.error_message,
                          created_at=row.created_at, digest=row.digest, parsed_name=row.parsed_name,
                          parse_status=row.parse_status, scope=scope_of(row), parsed_digest=row.parsed_digest,
                          digest_name=row.digest_name, digest_status=row.digest_status, digest_done=row.digest_done,
                          digest_total=row.digest_total, digest_digest=row.digest_digest, page_count=row.page_count)


def scope_of(row: Attachment) -> str:
    return row.store_scope or str(row.conversation_id)


def digest_name_of(filename: str) -> str:
    return replace_extension(filename, "导读.md")


def placement_failure(error: SandboxError) -> tuple[str, str]:
    if isinstance(error, SandboxTooLarge):
        return "sandbox_too_large", str(error) or "文件超过沙箱上限"
    return "sandbox_unavailable", str(error) or "沙箱暂时不可用"


async def lock_session(session: AsyncSession, conversation_id: str) -> None:
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                          {"key": f"attachment:{conversation_id}"})


class AttachmentService:
    def __init__(self, sessionmaker: async_sessionmaker, stores: SessionStores, sandbox: SessionSandbox,
                 settings: AttachmentSettings, parse_kind: JobKind, expire_kind: JobKind, digest_kind: JobKind, *,
                 parser_configured: Callable[[str], Awaitable[bool]]) -> None:
        self._sessionmaker = sessionmaker
        self._stores = stores
        self._sandbox = sandbox
        self._settings = settings
        self._parse_kind = parse_kind
        self._expire_kind = expire_kind
        self._digest_kind = digest_kind
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

    async def stage(self, workspace_id: str, user_id: str, files: Sequence[IncomingFile]) -> UploadResult:
        """传上来先挂在这个人名下（还没有会话），要解析的马上排解析；发消息时 claim 认领进会话。"""
        items: list[AttachmentView] = []
        failures: list[FileFailure] = []
        for incoming in files:
            try:
                items.append(view(await self._accept(workspace_id, user_id, incoming)))
            except AttachmentRejected as error:
                failures.append(FileFailure(_display(incoming.filename), error.code, error.message))
        return UploadResult(items=tuple(items), failures=tuple(failures))

    async def upload(self, conversation: Conversation, files: Sequence[IncomingFile]) -> UploadResult:
        """按会话上传（接口调用方用）：暂存后马上认领进这个会话，和网页发消息时同一条路。"""
        staged = await self.stage(conversation.workspace_id, conversation.user_id, files)
        claimed, failures = await self._claim(conversation, [item.id for item in staged.items])
        return UploadResult(items=claimed, failures=staged.failures + failures)

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

    async def _accept(self, workspace_id: str, user_id: str, incoming: IncomingFile) -> Attachment:
        name, kind, mime = self._validate(incoming)
        attachment_id = f"att_{uuid.uuid4().hex}"
        try:
            digest = await self._stores(workspace_id, attachment_id).put(incoming.data)
        except BlobIntegrityError as error:
            log.warning("attachment store failed: %s", type(error).__name__)
            raise AttachmentRejected("storage_unavailable", "文件存储暂时不可用，请稍后重试") from error
        # 原件总是原样放进沙箱（照官方：文件按原字节保存，模型用文件工具按需读）；PDF、Word、PPT 另外提取出一份同名
        # Markdown 放在旁边，方便读长文档；图片只在配了 MinerU 时识别文字（parse_job.py）。
        # 沙箱里的名字认领进会话时才定（要和会话里已有的文件错开），这里先记文件本来的名字
        parsed = kind in EXTRACTED_KINDS or (kind in OCR_KINDS and await self._parser_configured(workspace_id))
        # 纯文本附件的文字就是原件：放不进对话的长文本马上排导读（文档要等解析完才知道多长，见 queue_digest）。
        # 只做 Markdown、TXT：CSV、JSON 是数据，分段写要点没意义，问到了模型写代码去算、去查
        long_text = (kind is Kind.TEXT and extension(name) in PROSE_EXTENSIONS
                     and len(decode_text(incoming.data) or "") > self._settings.attachment_inline_chars)
        async with self._tx(workspace_id) as session:
            row = Attachment(id=attachment_id, workspace_id=workspace_id, conversation_id=None, user_id=user_id,
                             store_scope=attachment_id, filename=name, input_name=name,
                             parsed_name=replace_extension(name, "md") if parsed else None, kind=kind.value, mime=mime,
                             size=len(incoming.data), digest=digest, status=STAGED,
                             parse_status="parsing" if parsed else None, created_at=datetime.now(UTC),
                             **({"digest_name": digest_name_of(name), "digest_status": "digesting"} if long_text else {}))
            session.add(row)
            if parsed:
                await enqueue(session, self._parse_kind, workspace_id=workspace_id, subject_id=row.id)
            if long_text:
                await enqueue(session, self._digest_kind, workspace_id=workspace_id, subject_id=row.id)
            await enqueue(session, self._expire_kind, workspace_id=workspace_id, subject_id=row.id,
                          delay=self._settings.attachment_staged_ttl_hours * 3600)
        return row

    async def claim(self, conversation: Conversation, ids: Sequence[str]) -> tuple[AttachmentView, ...]:
        """本轮消息带的附件：这个人还没发出去的认领进这个会话、放进沙箱；已在这个会话里的照用。
        缺一个、放不进沙箱都整轮拒绝，免得模型以为有文件其实没有。"""
        wanted = list(dict.fromkeys(ids))
        if not wanted:
            return ()
        views, _ = await self._claim(conversation, wanted)
        found = {item.id: item for item in views}
        if any(i not in found or found[i].status != "ready" for i in wanted):
            raise AttachmentRejected(*UNAVAILABLE)
        return tuple(found[i] for i in wanted)

    async def _claim(self, conversation: Conversation,
                     ids: Sequence[str]) -> tuple[tuple[AttachmentView, ...], tuple[FileFailure, ...]]:
        if not ids:
            return (), ()
        claimed = await self._take(conversation, ids)
        views: list[AttachmentView] = []
        failures: list[FileFailure] = []
        for item in claimed:
            if not item.fresh:
                views.append(item.view)
                continue
            placed, failure = await self._place(conversation, item)
            views.append(placed)
            if failure is not None:
                failures.append(failure)
        return tuple(views), tuple(failures)

    async def _take(self, conversation: Conversation, ids: Sequence[str]) -> list[_Claimed]:
        """在一个事务里把还没发出去的认领进来：定沙箱里的名字（和会话里已有的错开）、填上会话。行锁住，和解析任务存结果互斥。

        要解析的文档这时就把导读的名字也定下：多长要解析完才知道，那时（queue_digest）已经在会话里，再起名就得另外
        和会话里的文件错开；先占下名字，用不上也只是个空名字。"""
        order = {attachment_id: index for index, attachment_id in enumerate(ids)}
        async with self._tx(conversation.workspace_id) as session:
            await lock_session(session, conversation.id)
            rows = (await session.scalars(select(Attachment).where(Attachment.id.in_(list(ids)))
                                          .with_for_update())).all()
            mine = sorted((row for row in rows if row.conversation_id == conversation.id or (
                row.conversation_id is None and row.status == STAGED and row.user_id == conversation.user_id)),
                key=lambda row: order[row.id])
            fresh = {row.id for row in mine if row.conversation_id is None}
            if fresh:
                names = (await session.execute(select(Attachment.input_name, Attachment.parsed_name,
                                                      Attachment.digest_name)
                                               .where(Attachment.conversation_id == conversation.id))).all()
                taken = [n for names_of_row in names for n in names_of_row if n]
                for row in mine:
                    if row.id in fresh:
                        row.input_name = unique_name(row.filename, taken)
                        taken.append(row.input_name)
                        if row.parsed_name:
                            row.parsed_name = unique_name(replace_extension(row.filename, "md"), taken)
                            taken.append(row.parsed_name)
                        if row.digest_name or row.parsed_name:
                            row.digest_name = unique_name(digest_name_of(row.filename), taken)
                            taken.append(row.digest_name)
                        row.conversation_id = conversation.id
                        row.status = "placing"
                await session.flush()
            return [_Claimed(view(row), scope_of(row), row.parsed_digest, row.digest_digest, row.id in fresh)
                    for row in mine]

    async def _place(self, conversation: Conversation, item: _Claimed) -> tuple[AttachmentView, FileFailure | None]:
        """原件放进沙箱；解析已经存下的 Markdown 一起放（还没解析完的由解析任务放）。"""
        workspace_id, row = conversation.workspace_id, item.view
        store = self._stores(workspace_id, item.scope)
        copies = [(row.input_name, row.digest, self.max_bytes)]
        if row.parsed_name and item.parsed_digest:
            copies.append((row.parsed_name, item.parsed_digest, self.max_bytes * MARKDOWN_FACTOR))
        if row.digest_name and item.digest_digest:
            copies.append((row.digest_name, item.digest_digest, self.max_bytes * MARKDOWN_FACTOR))
        try:
            for name, digest, cap in copies:
                await self._sandbox.put_input(workspace_id, conversation.id, name, await store.get(digest, max_bytes=cap))
        except BlobIntegrityError:
            code, message = "content_missing", "附件内容已不可用，请重新上传"
        except SandboxError as error:
            code, message = placement_failure(error)
        else:
            await self.mark(workspace_id, row.id, status="ready")
            return replace(row, status="ready"), None
        await self.mark(workspace_id, row.id, status="place_failed", code=code, message=message)
        failed = replace(row, status="place_failed", error_code=code, error_message=message)
        return failed, FileFailure(row.filename, code, message)

    @property
    def inline_limits(self) -> tuple[int, int]:
        """（单个附件、一轮合计）整篇放进这一轮的字数上限。"""
        return self._settings.attachment_inline_chars, self._settings.attachment_inline_total_chars

    async def readable_text(self, workspace_id: str, item: AttachmentView) -> str | None:
        """给模型读的文字：解析好的 Markdown，或纯文本附件本身；还没解析好、没有文字、读不出来的返回 None。"""
        if item.parse_status == "ready" and item.parsed_digest and item.parsed_name != item.input_name:
            digest, cap = item.parsed_digest, self.max_bytes * MARKDOWN_FACTOR
        elif item.kind == Kind.TEXT.value:
            digest, cap = item.digest, self.max_bytes
        else:
            return None
        try:
            data = await self._stores(workspace_id, item.scope).get(digest, max_bytes=cap)
        except BlobIntegrityError:
            return None
        return decode_text(data)

    async def states(self, workspace_id: str, user_id: str, ids: Sequence[str]) -> tuple[AttachmentView, ...]:
        """本人的这几个附件现在怎样（输入框等解析时查）；别人的、不存在的不返回。"""
        if not ids:
            return ()
        async with self._tx(workspace_id) as session:
            rows = (await session.scalars(select(Attachment).where(
                Attachment.id.in_(list(ids)), Attachment.user_id == user_id))).all()
        return tuple(view(row) for row in rows)

    async def discard(self, workspace_id: str, user_id: str, attachment_id: str) -> None:
        """输入框里移除了一个还没发出去的附件：取消它的任务，删行和文件。已经发出去的随会话一起删。"""
        async with self._tx(workspace_id) as session:
            row = await session.get(Attachment, attachment_id, with_for_update=True)
            if row is None or row.user_id != user_id:
                raise AttachmentRejected(*NOT_FOUND)
            if row.conversation_id is not None:
                raise AttachmentRejected(*ALREADY_SENT)
            await cancel(session, self._expire_kind, [row.id])
            removed = await self._remove(session, row)
        await self._delete_objects(workspace_id, [removed])

    async def expire(self, workspace_id: str, attachment_id: str) -> bool:
        """到期清理（expire_job.py）：一直没发出去的删掉；已经认领进会话的不动。返回删没删。"""
        async with self._tx(workspace_id) as session:
            row = await session.get(Attachment, attachment_id, with_for_update=True)
            if row is None or row.conversation_id is not None:
                return False
            removed = await self._remove(session, row)
        await self._delete_objects(workspace_id, [removed])
        return True

    async def _remove(self, session: AsyncSession, row: Attachment) -> tuple[str, set[str]]:
        await cancel(session, self._parse_kind, [row.id])
        await cancel(session, self._digest_kind, [row.id])
        await session.execute(delete(Attachment).where(Attachment.id == row.id))
        return scope_of(row), {d for d in (row.digest, row.parsed_digest, row.digest_digest) if d}

    async def _delete_objects(self, workspace_id: str, objects: Sequence[tuple[str, set[str]]]) -> None:
        for scope, digests in objects:
            store = self._stores(workspace_id, scope)
            for digest in digests:
                try:
                    await store.delete(digest)
                except BlobIntegrityError:
                    log.warning("failed to delete attachment object in %s", scope)

    async def parse_source(self, workspace_id: str, attachment_id: str) -> ParseSource | None:
        """解析任务开始时读附件；附件已删（会话删了、移除了、过期了）或不需要解析时返回 None。"""
        async with self._tx(workspace_id) as session:
            row = await session.get(Attachment, attachment_id)
        if row is None or not row.parsed_name:
            return None
        return ParseSource(filename=row.filename, kind=row.kind, mime=row.mime, digest=row.digest,
                           scope=scope_of(row), parsed_digest=row.parsed_digest)

    async def save_parsed(self, workspace_id: str, attachment_id: str, parsed_digest: str, *,
                          page_count: int | None = None) -> Placement | None:
        """解析结果一存下就记到附件上：之后放进沙箱失败重试、或沙箱重建时重放，都不用再调解析服务。
        锁住这一行再读它在哪个会话：已经发出去的，Markdown 由解析任务放进那个会话的沙箱；还没发出去的返回 None，
        发消息认领时会带上它。"""
        async with self._tx(workspace_id) as session:
            row = await session.get(Attachment, attachment_id, with_for_update=True)
            if row is None:
                return None
            row.parsed_digest = parsed_digest
            if page_count is not None:  # 重试时直接取上次存下的结果，数不出页数，保留上次记的
                row.page_count = page_count
            if row.conversation_id is None or not row.parsed_name:
                return None
            return Placement(conversation_id=row.conversation_id, parsed_name=row.parsed_name)

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

    async def queue_digest(self, session: AsyncSession, attachment_id: str, chars: int) -> None:
        """解析任务收尾的事务里调：文字放不进对话的长文档排导读（已经在做、做好了的不重排）。
        已经发出去的，导读的名字认领时定好了（_take）；还没发出去的先按文件名起，认领时再和会话里的错开。"""
        if chars <= self._settings.attachment_inline_chars:
            return
        row = await session.get(Attachment, attachment_id, with_for_update=True)
        if row is None or row.digest_status in ("digesting", "ready"):
            return
        row.digest_name = row.digest_name or digest_name_of(row.filename)
        row.digest_status, row.digest_done, row.digest_total = "digesting", None, None
        await enqueue(session, self._digest_kind, workspace_id=row.workspace_id, subject_id=row.id)

    async def digest_source(self, workspace_id: str, attachment_id: str) -> DigestSource | None:
        """导读任务开始时读：附件已删、不做导读、还没有可读文字时返回 None。"""
        async with self._tx(workspace_id) as session:
            row = await session.get(Attachment, attachment_id)
        if row is None or not row.digest_name:
            return None
        item = view(row)
        text = await self.readable_text(workspace_id, item)
        if text is None:
            return None
        separate = bool(item.parsed_name) and item.parsed_name != item.input_name and item.parse_status == "ready"
        return DigestSource(filename=row.filename, text_name=str(item.parsed_name if separate else item.input_name),
                            text=text, scope=item.scope)

    async def digest_progress(self, workspace_id: str, attachment_id: str, done: int, total: int) -> None:
        async with self._tx(workspace_id) as session:
            await session.execute(update(Attachment).where(Attachment.id == attachment_id)
                                  .values(digest_done=done, digest_total=total))

    async def save_digest(self, workspace_id: str, attachment_id: str, digest: str) -> Placement | None:
        """导读存下就记到附件上；和 save_parsed 一样锁住这一行，已经发出去的由导读任务放进那个会话的沙箱。"""
        async with self._tx(workspace_id) as session:
            row = await session.get(Attachment, attachment_id, with_for_update=True)
            if row is None:
                return None
            row.digest_digest = digest
            if row.conversation_id is None or not row.digest_name:
                return None
            return Placement(conversation_id=row.conversation_id, parsed_name=row.digest_name)

    async def apply_digest(self, session: AsyncSession, attachment_id: str, *, status: str) -> None:
        await session.execute(update(Attachment).where(Attachment.id == attachment_id)
                              .values(digest_status=status, updated_at=func.now()))

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
            copies.append((row.parsed_name, row.parsed_digest, self.max_bytes * MARKDOWN_FACTOR))
        if row.digest_name and row.digest_digest:
            copies.append((row.digest_name, row.digest_digest, self.max_bytes * MARKDOWN_FACTOR))
        store = self._stores(workspace_id, scope_of(row))
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
        """会话删除时调用：取消解析任务、删行，再删这些附件在对象存储里的原件与解析结果。

        运行中的解析任务收到取消标记后停下；万一它已经在收尾，写结果时附件行已不在，只是空操作。
        """
        async with self._tx(conversation.workspace_id) as session:
            rows = (await session.scalars(select(Attachment)
                                          .where(Attachment.conversation_id == conversation.id))).all()
            await cancel(session, self._parse_kind, [row.id for row in rows])
            await cancel(session, self._digest_kind, [row.id for row in rows])
            objects = [(scope_of(row), {d for d in (row.digest, row.parsed_digest, row.digest_digest) if d})
                       for row in rows]
            await session.execute(delete(Attachment).where(Attachment.conversation_id == conversation.id))
        await self._delete_objects(conversation.workspace_id, objects)


def _display(filename: str | None) -> str:
    return (filename or "")[-200:] or "（未命名文件）"
