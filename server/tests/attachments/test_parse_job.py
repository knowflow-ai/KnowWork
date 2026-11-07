"""附件解析任务：按错误类别重试、最终失败写回附件、放进沙箱失败保留解析结果、中断接手、会话删除取消。

领取与租约的通用行为在 tests/jobs 里测；这里只测附件这一种任务在框架上的表现。
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from knowwork_server.access.rls import set_workspace
from knowwork_server.attachments import AttachmentSettings, IncomingFile, ParseJob, build_attachments
from knowwork_server.attachments.mineru import MinerUError, MinerUOptions, corrupt, timed_out
from knowwork_server.attachments.models import Attachment
from knowwork_server.attachments.parse_job import CONVERT_SESSION, page_count
from knowwork_server.jobs import Job, JobRunner
from knowwork_server.sandbox.contract import SandboxConversionFailed, SandboxError, SandboxTooLarge
from knowwork_server.sessionfiles import local_session_stores
from knowwork_server.skills.blobstore import sha256_hex

from ..fake_parsers import EnvParsers
from ..fake_sandbox import FakeSandbox
from ..office_samples import DOCX, PDF, PNG, legacy_doc

pytestmark = pytest.mark.anyio
SETTINGS = AttachmentSettings(mineru_base_url="http://mineru", attachment_parse_retry_delay_seconds=30,
                              attachment_parse_lease_seconds=60)


class FakeMinerU:
    def __init__(self, *effects):
        self.effects = list(effects)
        self.calls: list[str] = []
        self.inputs: list[tuple[str, bytes, str]] = []

    async def parse(self, filename, data, mime):
        self.calls.append(filename)
        self.inputs.append((filename, data, mime))
        effect = self.effects.pop(0) if len(self.effects) > 1 else self.effects[0]
        if isinstance(effect, Exception):
            raise effect
        if callable(effect):
            return await effect()
        return effect


class Clock:
    def __init__(self):
        self.now = datetime.now(UTC) + timedelta(seconds=5)  # 晚于库里 now() 写下的排队时间

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


class Fixed:
    """固定给出一个 MinerU 客户端的提供者；mineru 为 None 表示没配置。"""

    def __init__(self, mineru):
        self.mineru = mineru

    async def get(self, workspace_id, options):
        return self.mineru


# 提取出来几乎没有字的 PDF（扫描件）：这些测试的 PDF 都当扫描件，走 MinerU 识别，测的是 MinerU 那条路上的重试与失败
SCANNED = b"\f"


def make(files_db, tmp_path, mineru, settings=SETTINGS):
    sandbox, stores, clock = FakeSandbox(extracted=SCANNED), local_session_stores(tmp_path), Clock()
    attachments = build_attachments(files_db.sessionmaker, stores, sandbox, settings, parsers=EnvParsers(settings))
    runner = JobRunner(files_db.sessionmaker, clock=clock)
    ParseJob(attachments.service, stores, sandbox, Fixed(mineru), MinerUOptions(), settings).register(runner)
    return attachments.service, runner, sandbox, stores, clock


async def queued(service, conv, name="报告.pdf", data=PDF):
    result = await service.upload(conv, [IncomingFile(name, data)])
    assert result.failures == ()
    return result.items[0]


async def test_success_puts_markdown_into_sandbox(files_db, tmp_path):
    conv = await files_db.conversation()
    service, runner, sandbox, stores, _ = make(files_db, tmp_path, FakeMinerU("# 报告\n正文"))
    await queued(service, conv)
    assert (await runner.run_once("ws1")).status == "succeeded"
    assert sandbox.inputs[("ws1", conv.id)] == {"报告.pdf": PDF, "报告.md": "# 报告\n正文".encode()}, "原件旁边多一份 Markdown"
    [item] = await service.list(conv)
    assert (item.status, item.parsed_name, item.parse_status) == ("ready", "报告.md", "ready")
    assert await stores("ws1", item.id).get(sha256_hex("# 报告\n正文".encode()), max_bytes=100), "存在附件自己的命名空间里"
    assert await runner.run_once("ws1") is None  # 没活了


async def test_office_files_and_text_pdfs_are_extracted_in_the_sandbox_without_mineru(files_db, tmp_path):
    """PDF、Word、PPT 在转换沙箱里直接提取文字，一两秒就好，不经 MinerU（没配也行）；PDF 的换页标成「[第 n 页]」。"""
    conv = await files_db.conversation()
    service, runner, sandbox, _, _ = make(files_db, tmp_path, None)
    sandbox.extracted = None  # 提取出像样的正文
    await queued(service, conv, "方案.docx", DOCX)
    assert (await runner.run_once("ws1")).status == "succeeded"
    sandbox.extracted = "第一页的正文，足够长。".encode() * 10 + b"\f" + "第二页。".encode() * 20 + b"\f"
    await queued(service, conv, "报告.pdf", PDF)
    assert (await runner.run_once("ws1")).status == "succeeded"
    assert sandbox.extractions == [(CONVERT_SESSION, "docx"), (CONVERT_SESSION, "pdf")], "暂存时还没有会话，在转换沙箱里提取"
    placed = sandbox.inputs[("ws1", conv.id)]
    assert placed["方案.md"].startswith("# 从 docx 直接提取".encode())
    pdf_text = placed["报告.md"].decode()
    assert pdf_text.startswith("[第 1 页]\n第一页") and "\n\n[第 2 页]\n第二页" in pdf_text and "\f" not in pdf_text
    assert [(v.filename, v.parse_status) for v in await service.list(conv)] == [("方案.docx", "ready"), ("报告.pdf", "ready")]


async def test_scanned_pdf_goes_to_mineru_and_needs_it(files_db, tmp_path):
    """提取出来每页没几个字的 PDF 是扫描件：交给 MinerU 识别；没配 MinerU 就说清要配，不重试，原件照样能用。"""
    conv = await files_db.conversation()
    mineru = FakeMinerU("# 扫描件识别出的字")
    service, runner, sandbox, _, _ = make(files_db, tmp_path, mineru)
    await queued(service, conv, "扫描.pdf", PDF)
    assert (await runner.run_once("ws1")).status == "succeeded"
    assert mineru.calls == ["扫描.pdf"] and sandbox.inputs[("ws1", conv.id)]["扫描.md"] == "# 扫描件识别出的字".encode()
    service, runner, sandbox, _, _ = make(files_db, tmp_path, None)
    item = await queued(service, conv, "又一份扫描.pdf", PDF)
    outcome = await runner.run_once("ws1")
    assert (outcome.status, outcome.error_code) == ("failed", "parser_not_configured")
    [state] = [v for v in await service.list(conv) if v.id == item.id]
    assert state.status == "ready" and "扫描件" in state.error_message


async def test_legacy_word_is_converted_then_extracted(files_db, tmp_path):
    """旧格式 Word 先在转换沙箱里用 LibreOffice 转成 docx，再提取文字；会话沙箱里放的仍是原件。"""
    conv = await files_db.conversation()
    mineru = FakeMinerU("# 不该走到这里")
    service, runner, sandbox, _, _ = make(files_db, tmp_path, mineru)
    sandbox.extracted = None
    original = legacy_doc()
    await queued(service, conv, "旧合同.doc", original)
    assert (await runner.run_once("ws1")).status == "succeeded"
    assert sandbox.conversions == [(CONVERT_SESSION, "doc")] and sandbox.extractions == [(CONVERT_SESSION, "docx")]
    assert mineru.calls == []
    placed = sandbox.inputs[("ws1", conv.id)]
    assert placed["旧合同.doc"] == original and placed["旧合同.md"].startswith("# 从 docx 直接提取".encode())
    [item] = await service.list(conv)
    assert (item.kind, item.parse_status) == ("doc", "ready")


@pytest.mark.parametrize(("error", "status", "code"), [
    (SandboxConversionFailed("pdftotext 打不开"), "failed", "file_corrupt"),
    (SandboxTooLarge("超出上限"), "failed", "too_large"),
    (SandboxError("沙箱暂时不可用"), "retry", "extract_unavailable"),
])
async def test_extraction_failures(files_db, tmp_path, error, status, code):
    """提取不出来：打不开（损坏、加密）、太大是文件问题，不重试；沙箱一时不可用按服务问题重试。"""
    conv = await files_db.conversation()
    service, runner, sandbox, _, _ = make(files_db, tmp_path, FakeMinerU("x"))
    sandbox.fail_extract = error
    await queued(service, conv, "方案.docx", DOCX)
    outcome = await runner.run_once("ws1")
    assert (outcome.status, outcome.error_code) == (status, code)


async def test_images_go_to_mineru_as_they_are(files_db, tmp_path):
    """图片（截图、拍的合同）MinerU 原生支持：原件直接交给它识别出文字，能看图的模型另外可以直接看沙箱里的原图。"""
    conv = await files_db.conversation()
    mineru = FakeMinerU("付款期限：30 天")
    service, runner, sandbox, _, _ = make(files_db, tmp_path, mineru)
    await queued(service, conv, "截图.png", PNG)
    assert (await runner.run_once("ws1")).status == "succeeded"
    assert mineru.inputs == [("截图.png", PNG, "image/png")] and sandbox.conversions == []
    assert sandbox.inputs[("ws1", conv.id)] == {"截图.png": PNG, "截图.md": "付款期限：30 天".encode()}


@pytest.mark.parametrize(("error", "status", "code"), [
    (SandboxConversionFailed("LibreOffice 打不开"), "failed", "file_corrupt"),
    (SandboxTooLarge("超出转换上限"), "failed", "too_large"),
    (SandboxError("沙箱暂时不可用"), "retry", "convert_unavailable"),
])
async def test_legacy_word_that_cannot_be_converted(files_db, tmp_path, error, status, code):
    """转不出来：文件坏了、加了密码、太大是文件问题，不重试，提示另存为 .docx；超时、沙箱一时不可用按服务问题重试。"""
    conv = await files_db.conversation()
    mineru = FakeMinerU("# 不该走到这里")
    service, runner, sandbox, _, _ = make(files_db, tmp_path, mineru)
    sandbox.fail_convert = error
    await queued(service, conv, "旧合同.doc", legacy_doc())
    outcome = await runner.run_once("ws1")
    assert (outcome.status, outcome.error_code) == (status, code)
    assert mineru.calls == []
    if status == "failed":
        [item] = await service.list(conv)
        assert item.parse_status == "failed" and "另存为 .docx" in item.error_message


async def test_timeouts_retry_twice_then_fail(files_db, tmp_path):
    conv = await files_db.conversation()
    mineru = FakeMinerU(timed_out())
    service, runner, _, _, clock = make(files_db, tmp_path, mineru)
    await queued(service, conv)
    assert (await runner.run_once("ws1")).status == "retry"
    assert await runner.run_once("ws1") is None  # 退避期内不领
    clock.advance(31)
    assert (await runner.run_once("ws1")).status == "retry"
    clock.advance(61)
    outcome = await runner.run_once("ws1")
    assert (outcome.status, outcome.error_code) == ("failed", "timeout")
    assert len(mineru.calls) == 3
    [item] = await service.list(conv)
    assert (item.status, item.parse_status, item.error_code, item.error_message) == (
        "ready", "failed", "timeout", "文档解析服务超时"), "解析失败不影响原件可用"


@pytest.mark.parametrize("error", [corrupt(), MinerUError("unsupported_format", "不支持", retryable=False),
                                   MinerUError("parser_version_unsupported", "版本不受支持", retryable=False)])
async def test_permanent_errors_do_not_retry(files_db, tmp_path, error):
    conv = await files_db.conversation()
    mineru = FakeMinerU(error)
    service, runner, _, _, clock = make(files_db, tmp_path, mineru)
    await queued(service, conv)
    assert (await runner.run_once("ws1")).status == "failed"
    clock.advance(3600)
    assert await runner.run_once("ws1") is None and len(mineru.calls) == 1
    [item] = await service.list(conv)
    assert (item.parse_status, item.error_code) == ("failed", error.code)


async def test_task_failure_without_file_hint_is_retried(files_db, tmp_path):
    """W9：MinerU 任务失败但看不出是文件问题（例如服务端配置错），按服务问题重试，不直接判文件坏了。"""
    conv = await files_db.conversation()
    mineru = FakeMinerU(MinerUError("parser_misconfigured", "解析服务出了问题", retryable=True), "# 好了")
    service, runner, _, _, clock = make(files_db, tmp_path, mineru)
    await queued(service, conv)
    assert (await runner.run_once("ws1")).status == "retry"
    clock.advance(31)
    assert (await runner.run_once("ws1")).status == "succeeded"


async def test_unconfigured_parser_and_missing_content(files_db, tmp_path):
    conv = await files_db.conversation()
    service, runner, _, stores, _ = make(files_db, tmp_path, None)
    await queued(service, conv)
    assert (await runner.run_once("ws1")).error_code == "parser_not_configured"
    service, runner, _, stores, _ = make(files_db, tmp_path, FakeMinerU("x"))
    item = await queued(service, conv, "b.docx", DOCX)
    await stores("ws1", item.id).delete(sha256_hex(DOCX))
    assert (await runner.run_once("ws1")).error_code == "content_missing"


async def test_place_failure_retries_without_parsing_again(files_db, tmp_path):
    """放进沙箱失败（例如 api 进程正在建同一个会话容器）按服务问题重试；解析结果已记下，重试不再调解析服务。"""
    conv = await files_db.conversation()
    mineru = FakeMinerU("# md")
    service, runner, sandbox, _, clock = make(files_db, tmp_path, mineru)
    sandbox.fail_put = {"报告.md": SandboxError("沙箱正在准备中，请稍后重试")}
    await queued(service, conv)
    outcome = await runner.run_once("ws1")
    assert (outcome.status, outcome.error_code) == ("retry", "sandbox_unavailable")
    [item] = await service.list(conv)
    assert item.parse_status == "parsing", "还在重试，界面上仍是解析中"
    sandbox.fail_put = {}
    clock.advance(31)
    assert (await runner.run_once("ws1")).status == "succeeded"
    assert len(mineru.calls) == 1
    assert sandbox.inputs[("ws1", conv.id)] == {"报告.pdf": PDF, "报告.md": b"# md"}


async def test_place_failure_keeps_parsed_content_for_restage(files_db, tmp_path):
    conv = await files_db.conversation()
    settings = AttachmentSettings(mineru_base_url="http://m", attachment_parse_max_attempts=1)
    service, runner, sandbox, _, _ = make(files_db, tmp_path, FakeMinerU("# md"), settings)
    sandbox.fail_put = {"报告.md": SandboxError("沙箱暂时不可用")}
    await queued(service, conv)
    outcome = await runner.run_once("ws1")
    assert (outcome.status, outcome.error_code) == ("failed", "sandbox_unavailable")
    [item] = await service.list(conv)
    assert (item.status, item.parse_status, item.error_message) == ("ready", "failed", "沙箱暂时不可用")
    sandbox.fail_put = {}
    assert await service.restage("ws1", conv.id) == ()
    assert sandbox.inputs[("ws1", conv.id)] == {"报告.pdf": PDF, "报告.md": b"# md"}, "解析出的内容还在，重放时补上"


async def test_repeatedly_interrupted_job_gives_up(files_db, tmp_path):
    conv = await files_db.conversation()
    settings = AttachmentSettings(mineru_base_url="http://m", attachment_parse_max_attempts=1,
                                  attachment_parse_lease_seconds=60)
    service, runner, _, _, clock = make(files_db, tmp_path, FakeMinerU("x"), settings)
    await queued(service, conv)
    await runner.claim("ws1")  # worker 领到后被杀
    clock.advance(61)
    outcome = await runner.run_once("ws1")
    assert (outcome.status, outcome.error_code) == ("failed", "interrupted")
    [item] = await service.list(conv)
    assert (item.parse_status, item.error_message) == ("failed", "文档解析多次中断，请重新上传")


async def test_erasing_the_conversation_cancels_a_running_parse(files_db, tmp_path):
    conv = await files_db.conversation()
    started, release = asyncio.Event(), asyncio.Event()

    async def slow():
        started.set()
        await release.wait()
        return "# 晚了"

    settings = AttachmentSettings(mineru_base_url="http://m", attachment_parse_lease_seconds=0.3)
    service, runner, sandbox, _, _ = make(files_db, tmp_path, FakeMinerU(slow), settings)
    await queued(service, conv)
    run = asyncio.create_task(runner.run_once("ws1"))
    await started.wait()
    await service.erase(conv)
    assert (await asyncio.wait_for(run, 5)).status == "canceled"
    assert sandbox.inputs[("ws1", conv.id)] == {"报告.pdf": PDF}, "Markdown 没有再放进去"


async def test_deleted_attachment_ends_the_job_quietly(files_db, tmp_path):
    conv = await files_db.conversation()
    service, runner, _, _, _ = make(files_db, tmp_path, FakeMinerU("x"))
    item = await queued(service, conv)
    async with files_db.sessionmaker() as session, session.begin():
        await set_workspace(session, "ws1")
        await session.delete(await session.get(Attachment, item.id))  # 绕过 erase，模拟取消前就删了
    assert (await runner.run_once("ws1")).status == "succeeded"
    async with files_db.sessionmaker() as session, session.begin():
        await set_workspace(session, "ws1")
        assert (await session.scalar(select(Job.status).where(Job.kind == "attachment.parse"))) == "succeeded"


async def test_legacy_rows_restage_only_the_markdown(files_db, tmp_path):
    """迁移 0013 之前的 PDF、Word、PPT 只放了 Markdown（迁移后 parsed_name 等于 input_name），重放时照旧只放它。"""
    conv = await files_db.conversation()
    service, runner, sandbox, _, _ = make(files_db, tmp_path, FakeMinerU("# 旧"))
    item = await queued(service, conv)
    await runner.run_once("ws1")
    async with files_db.sessionmaker() as session, session.begin():
        await set_workspace(session, "ws1")
        await session.execute(update(Attachment).where(Attachment.id == item.id)
                              .values(input_name="报告.md", parsed_name="报告.md"))
    sandbox.inputs.clear()
    assert await service.restage("ws1", conv.id) == ()
    assert sandbox.inputs[("ws1", conv.id)] == {"报告.md": "# 旧".encode()}


def test_page_count_is_the_form_feeds_pdftotext_writes():
    assert (page_count(b"a\fb\f"), page_count(b"\f\f\f"), page_count(b"no pages")) == (2, 3, None)
