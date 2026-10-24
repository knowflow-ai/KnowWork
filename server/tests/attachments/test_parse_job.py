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
from knowwork_server.jobs import Job, JobRunner
from knowwork_server.sandbox.contract import SandboxError
from knowwork_server.sessionfiles import local_session_stores
from knowwork_server.skills.blobstore import sha256_hex

from ..fake_parsers import EnvParsers
from ..fake_sandbox import FakeSandbox
from ..office_samples import DOCX, PDF

pytestmark = pytest.mark.anyio
SETTINGS = AttachmentSettings(mineru_base_url="http://mineru", attachment_parse_retry_delay_seconds=30,
                              attachment_parse_lease_seconds=60)


class FakeMinerU:
    def __init__(self, *effects):
        self.effects = list(effects)
        self.calls: list[str] = []

    async def parse(self, filename, data, mime):
        self.calls.append(filename)
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


def make(files_db, tmp_path, mineru, settings=SETTINGS):
    sandbox, stores, clock = FakeSandbox(), local_session_stores(tmp_path), Clock()
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
    assert await stores("ws1", conv.id).get(sha256_hex("# 报告\n正文".encode()), max_bytes=100)
    assert await runner.run_once("ws1") is None  # 没活了


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
    await queued(service, conv, "b.docx", DOCX)
    await stores("ws1", conv.id).delete(sha256_hex(DOCX))
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
        assert (await session.scalar(select(Job.status))) == "succeeded"


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
