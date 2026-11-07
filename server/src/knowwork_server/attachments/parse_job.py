"""附件解析任务（通用任务框架上的 attachment.parse）：取原件 → 提取文字 → 存 Markdown →（已经发出去的）放进会话沙箱。

提取文字照 Agent 产品读附件的通行做法走轻量的：PDF、Word、PPT 在转换沙箱里直接提取（pdftotext、pandoc、markitdown，
一两秒就好；旧格式 Word 先转 docx），不经版面模型。要识别文字的才交给 MinerU：图片，以及提取出来每页没几个字的 PDF
（扫描件）。私有化部署的模型多半看不了图，这一步省不了。原来一律走 MinerU，3 页的文档也要十几秒到半分钟。

附件在输入框里一选就传上来、马上排解析，这时多半还没有会话：结果只存下来，发消息认领时连同原件一起放进沙箱；
解析比发消息慢的，由这里放进认领它的那个会话（service.save_parsed 锁住附件行告诉这里放到哪，不会漏放）。

领取、续租、重试、超时、停机都由任务框架负责；这里只管四个阶段和失败分类：
- 附件已删（会话删了、输入框里移除了、过期了）：什么都不做，任务直接结束；
- 扫描件、图片要识别文字却没配置解析服务：不重试；原件内容丢了：文件问题，不重试；
- 提取不出来（损坏、加了密码、太大）：文件问题，不重试；提取超时、沙箱一时不可用：服务问题，重试；
- MinerU 报格式不支持、文件损坏：文件问题，不重试；超时、不可用、任务失败但看不出是文件问题：服务问题，重试；
- .doc 转不出来（损坏、加了密码、太大）：文件问题，不重试，提示另存为 .docx；转换超时、沙箱一时不可用：服务问题，重试
  （和知识库入库时转 .doc 同一套分法）；
- 放进沙箱失败（容器正被 api 进程创建、Docker 暂时不可用）：服务问题，重试。解析结果在 store 阶段已经
  记到附件上，重试时直接取，不再调解析服务；重试用尽时 Markdown 也还在，沙箱重建时重放（restage）补上。
  超过沙箱上限的不重试。
最终失败时把附件的 parse_status 标成 failed（和任务收尾同一事务）；原件不受影响，照样可用。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from ..jobs import Claim, Done, JobContext, JobError, JobFailure, JobKind, JobRunner
from ..sandbox.contract import SandboxConversionFailed, SandboxError, SandboxTooLarge, SessionSandbox
from ..sessionfiles.storage import SessionStores
from ..skills.errors import BlobIntegrityError
from .mineru import MinerUError, MinerUOptions, MinerUProvider
from .service import AttachmentService, ParseSource, placement_failure
from .settings import AttachmentSettings
from .types import CONVERTED_KINDS, OCR_KINDS, Kind

KIND_NAME = "attachment.parse"
# 用户在对话里等着结果，排在批量导入、同步这类任务之前（迁移 0017 搬旧任务时用的也是这个值）
PARSE_PRIORITY = 10
_FILE_CODES = frozenset({"unsupported_format", "file_corrupt"})
CONVERT_TIMEOUT_SECONDS = 180.0  # 与知识库转 .doc 一致
_SAVE_AS_DOCX = "请用 Word 另存为 .docx 再上传"
# 转 .doc、提取文字用的沙箱：每个工作空间一个，不占用户会话的（暂存时还没有会话），容器空闲后照常回收
CONVERT_SESSION = "attachment-convert"
# PDF 提取出来平均每页不到这么多个字（不算空白）就当扫描件，交给 MinerU 识别
SCANNED_CHARS_PER_PAGE = 50
NEEDS_OCR = ("parser_not_configured", "这份文件是扫描件或图片，要配文档解析服务（MinerU）才能识别里面的字")
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
        pages = None
        if markdown is None:
            markdown, digest, pages = await self._parse(ctx, source)
        else:
            digest = str(source.parsed_digest)
        placement = await self._service.save_parsed(ctx.workspace_id, ctx.subject_id, digest, page_count=pages)
        if placement is not None:
            await self._place(ctx, placement.conversation_id, placement.parsed_name, markdown)
        attachment_id = ctx.subject_id
        chars = len(markdown.decode("utf-8", errors="replace"))

        async def apply(session: AsyncSession) -> None:
            await self._service.apply_parse(session, attachment_id, parse_status="ready")
            await self._service.queue_digest(session, attachment_id, chars)  # 放不进对话的长文档接着做导读

        return Done(apply=apply)

    async def _place(self, ctx: JobContext, conversation_id: str, name: str, markdown: bytes) -> None:
        async with ctx.stage("place"):
            try:
                await self._sandbox.put_input(ctx.workspace_id, conversation_id, name, markdown)
            except SandboxError as error:
                code, message = placement_failure(error)
                raise JobError(code, message, error_class="file" if isinstance(error, SandboxTooLarge) else "service",
                               detail=str(error)) from error

    async def _parsed_before(self, ctx: JobContext, source: ParseSource) -> bytes | None:
        if not source.parsed_digest:
            return None
        try:  # Markdown 可能比原件大（表格展开），上限放宽到 4 倍，与 restage 一致
            return await self._stores(ctx.workspace_id, source.scope).get(
                source.parsed_digest, max_bytes=self._settings.attachment_max_bytes * 4)
        except BlobIntegrityError:
            return None  # 存的结果丢了，重新解析

    async def _parse(self, ctx: JobContext, source: ParseSource) -> tuple[bytes, str, int | None]:
        """提取（或识别）文字并存下，返回 Markdown、它的摘要和页数（只有 PDF 有）。"""
        store = self._stores(ctx.workspace_id, source.scope)
        try:
            original = await store.get(source.digest, max_bytes=self._settings.attachment_max_bytes)
        except BlobIntegrityError as error:
            raise JobError("content_missing", "附件内容已不可用，请重新上传", error_class="file") from error
        pages = None
        if source.kind in OCR_KINDS:
            markdown = await self._ocr(ctx, source.filename, original, source.mime)
        else:
            text = await self._extract(ctx, source, original)
            pages = page_count(text) if source.kind == Kind.PDF else None
            if source.kind != Kind.PDF:
                markdown = text
            elif scanned(text):
                markdown = await self._ocr(ctx, source.filename, original, source.mime)
            else:
                markdown = paged(text)
        async with ctx.stage("store") as stage:
            try:
                parsed_digest = await store.put(markdown)
            except BlobIntegrityError as error:
                raise JobError("storage_unavailable", "文件存储暂时不可用") from error
            stage.record(bytes=len(markdown))
        return markdown, parsed_digest, pages

    async def _extract(self, ctx: JobContext, source: ParseSource, original: bytes) -> bytes:
        extension, data = source.kind, original
        if source.kind in CONVERTED_KINDS:
            extension, data = "docx", await self._to_docx(ctx, source, original)
        async with ctx.stage("extract") as stage:
            try:
                text = await self._sandbox.extract_text(
                    ctx.workspace_id, CONVERT_SESSION, extension, data,
                    max_bytes=self._settings.attachment_max_bytes * 4, timeout_seconds=CONVERT_TIMEOUT_SECONDS)
            except SandboxTooLarge as error:
                raise JobError("too_large", "文件太大，提取不了文字", error_class="file") from error
            except SandboxConversionFailed as error:
                raise JobError("file_corrupt", "文件打不开，可能已损坏或加了密码", error_class="file",
                               detail=str(error)) from error
            except SandboxError as error:
                # 超时、沙箱内存不够、沙箱暂时不可用：按服务问题重试
                raise JobError("extract_unavailable", f"提取文字没成功（{error}）") from error
            stage.record(bytes=len(text))
        return text

    async def _ocr(self, ctx: JobContext, filename: str, data: bytes, mime: str) -> bytes:
        mineru = await self._mineru.get(ctx.workspace_id, self._options)  # 管理后台改了配置，下一次就用新的
        if mineru is None:
            raise JobError(*NEEDS_OCR, retryable=False)
        async with ctx.stage("ocr") as stage:
            try:
                markdown = (await mineru.parse(filename, data, mime)).encode("utf-8")
            except MinerUError as error:
                raise _job_error(error) from error
            stage.record(bytes=len(data))
        return markdown

    async def _to_docx(self, ctx: JobContext, source: ParseSource, original: bytes) -> bytes:
        """旧格式 Word 在转换沙箱里用 LibreOffice 转成 docx 再提取。转出的只拿来提取，会话沙箱里放的仍是用户传的原件。"""
        async with ctx.stage("to_docx") as stage:
            try:
                docx = await self._sandbox.convert_doc_to_docx(
                    ctx.workspace_id, CONVERT_SESSION, original,
                    max_bytes=self._settings.attachment_max_bytes * 4, timeout_seconds=CONVERT_TIMEOUT_SECONDS)
            except SandboxTooLarge as error:
                raise JobError("too_large", f"这份 Word（.doc）太大，转换不了；{_SAVE_AS_DOCX}",
                               error_class="file") from error
            except SandboxConversionFailed as error:
                raise JobError("file_corrupt", f"这份 Word（.doc）转不成 .docx，文件可能已损坏或加了密码；{_SAVE_AS_DOCX}",
                               error_class="file", detail=str(error)) from error
            except SandboxError as error:
                # 超时、沙箱内存不够、沙箱暂时不可用：按服务问题重试
                raise JobError("convert_unavailable", f"旧格式 Word 转换没成功（{error}）") from error
            stage.record(bytes=len(docx))
        return docx

    async def give_up(self, session: AsyncSession, claim: Claim, failure: JobFailure) -> None:
        await self._service.apply_parse(session, claim.subject_id, parse_status="failed", code=failure.code,
                                        message=_MESSAGES.get(failure.code, failure.message))


def page_count(text: bytes) -> int | None:
    """pdftotext 每页后面跟一个换页符，扫描件提取不出字也照样有，数出来就是页数。"""
    return text.count(b"\f") or None


def scanned(text: bytes) -> bool:
    """pdftotext 每页后面跟一个换页符：平均每页不算空白不到 SCANNED_CHARS_PER_PAGE 个字，多半是扫描件。"""
    decoded = text.decode("utf-8", errors="replace")
    pages = max(1, decoded.count("\f"))
    return len("".join(decoded.split())) < SCANNED_CHARS_PER_PAGE * pages


def paged(text: bytes) -> bytes:
    """换页符换成「[第 n 页]」：模型回答「第几页写了什么」、用户核对时都用得上。"""
    pages = text.decode("utf-8", errors="replace").split("\f")
    if pages and not pages[-1].strip():
        pages = pages[:-1]
    return "\n\n".join(f"[第 {n} 页]\n{page.strip(chr(10))}" for n, page in enumerate(pages, 1)).encode("utf-8")
