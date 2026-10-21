"""解析 worker：SKIP LOCKED 领取、按错误类型重试、租约过期重领、过期的领取不得覆盖结果。"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from knowwork_server.access.rls import set_workspace
from knowwork_server.attachments import AttachmentSettings, IncomingFile, ParseWorker, build_attachments
from knowwork_server.attachments.mineru import MinerUError, corrupt, timed_out
from knowwork_server.attachments.models import Attachment
from knowwork_server.sandbox.contract import SandboxError
from knowwork_server.sessionfiles import local_session_stores
from knowwork_server.skills.blobstore import sha256_hex

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
        return effect


class Clock:
    def __init__(self):
        self.now = datetime.now(UTC) + timedelta(seconds=5)  # 晚于库里 now() 写下的排队时间

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


def make(files_db, tmp_path, mineru, settings=SETTINGS):
    sandbox, stores, clock = FakeSandbox(), local_session_stores(tmp_path), Clock()
    attachments = build_attachments(files_db.sessionmaker, stores, sandbox, settings)
    worker = ParseWorker(files_db.sessionmaker, stores, sandbox, attachments.service, mineru, settings, clock=clock)
    return attachments.service, worker, sandbox, stores, clock


async def queued(service, conv, name="报告.pdf", data=PDF):
    result = await service.upload(conv, [IncomingFile(name, data)])
    assert result.failures == ()
    return result.items[0]


async def test_success_puts_markdown_into_sandbox(files_db, tmp_path):
    conv = await files_db.conversation()
    service, worker, sandbox, stores, _ = make(files_db, tmp_path, FakeMinerU("# 报告\n正文"))
    await queued(service, conv)
    assert (await worker.run_one("ws1")).status == "succeeded"
    assert sandbox.inputs[("ws1", conv.id)] == {"报告.pdf": PDF, "报告.md": "# 报告\n正文".encode()}, "原件旁边多一份 Markdown"
    [item] = await service.list(conv)
    assert (item.status, item.parsed_name, item.parse_status) == ("ready", "报告.md", "ready")
    assert await stores("ws1", conv.id).get(sha256_hex("# 报告\n正文".encode()), max_bytes=100)
    assert await worker.run_one("ws1") is None  # 没活了


async def test_timeouts_retry_twice_then_fail(files_db, tmp_path):
    conv = await files_db.conversation()
    mineru = FakeMinerU(timed_out())
    service, worker, _, _, clock = make(files_db, tmp_path, mineru)
    await queued(service, conv)
    assert (await worker.run_one("ws1")).status == "retry"
    assert await worker.run_one("ws1") is None  # 退避期内不领
    clock.advance(31)
    assert (await worker.run_one("ws1")).status == "retry"
    clock.advance(61)
    outcome = await worker.run_one("ws1")
    assert (outcome.status, outcome.error_code) == ("failed", "timeout")
    assert len(mineru.calls) == 3
    [item] = await service.list(conv)
    assert (item.status, item.parse_status, item.error_code, item.error_message) == (
        "ready", "failed", "timeout", "文档解析服务超时"), "解析失败不影响原件可用"


@pytest.mark.parametrize("error", [corrupt(), MinerUError("unsupported_format", "不支持", retryable=False)])
async def test_permanent_errors_do_not_retry(files_db, tmp_path, error):
    conv = await files_db.conversation()
    mineru = FakeMinerU(error)
    service, worker, _, _, clock = make(files_db, tmp_path, mineru)
    await queued(service, conv)
    assert (await worker.run_one("ws1")).status == "failed"
    clock.advance(3600)
    assert await worker.run_one("ws1") is None and len(mineru.calls) == 1


async def test_unconfigured_parser_and_missing_content(files_db, tmp_path):
    conv = await files_db.conversation()
    service, worker, _, stores, _ = make(files_db, tmp_path, None)
    await queued(service, conv)
    assert (await worker.run_one("ws1")).error_code == "parser_not_configured"
    service, worker, _, stores, _ = make(files_db, tmp_path, FakeMinerU("x"))
    await queued(service, conv, "b.docx", DOCX)
    await stores("ws1", conv.id).delete(sha256_hex(DOCX))
    assert (await worker.run_one("ws1")).error_code == "content_missing"


async def test_place_failure_keeps_parsed_content_for_restage(files_db, tmp_path):
    conv = await files_db.conversation()
    service, worker, sandbox, _, _ = make(files_db, tmp_path, FakeMinerU("# md"))
    sandbox.fail_put = {"报告.md": SandboxError("沙箱暂时不可用")}
    await queued(service, conv)
    outcome = await worker.run_one("ws1")
    assert (outcome.status, outcome.error_code) == ("succeeded", "sandbox_unavailable")
    [item] = await service.list(conv)
    assert (item.status, item.parse_status, item.error_message) == ("ready", "failed", "沙箱暂时不可用")
    sandbox.fail_put = {}
    assert await service.restage("ws1", conv.id) == ()
    assert sandbox.inputs[("ws1", conv.id)] == {"报告.pdf": PDF, "报告.md": b"# md"}, "解析出的内容还在，重放时补上"


async def test_concurrent_claims_skip_locked_rows(files_db, tmp_path):
    conv = await files_db.conversation()
    service, worker, _, _, _ = make(files_db, tmp_path, FakeMinerU("x"))
    await queued(service, conv, "a.pdf")
    await queued(service, conv, "b.pdf")
    first, second, third = await asyncio.gather(worker.claim("ws1"), worker.claim("ws1"), worker.claim("ws1"))
    claimed = [c for c in (first, second, third) if c is not None]
    assert sorted(c.filename for c in claimed) == ["a.pdf", "b.pdf"]
    assert await worker.claim("ws2") is None  # 别的空间看不到


async def test_expired_lease_is_reclaimed_and_stale_owner_cannot_overwrite(files_db, tmp_path):
    conv = await files_db.conversation()
    service, worker, _sandbox, _, clock = make(files_db, tmp_path, FakeMinerU("# 新"))
    await queued(service, conv)
    stale = await worker.claim("ws1")  # 这个 worker 卡住了
    assert await worker.claim("ws1") is None
    clock.advance(61)
    assert (await worker.run_one("ws1")).status == "succeeded"  # 另一个 worker 接手
    assert not await worker._finish("ws1", stale, "failed", "x")  # 旧领取者回来，写不进去
    [item] = await service.list(conv)
    assert item.status == "ready"
    assert stale.attempts == 1


async def test_repeatedly_interrupted_job_gives_up(files_db, tmp_path):
    conv = await files_db.conversation()
    settings = AttachmentSettings(mineru_base_url="http://m", attachment_parse_max_attempts=1,
                                  attachment_parse_lease_seconds=60)
    service, worker, _, _, clock = make(files_db, tmp_path, FakeMinerU("x"), settings)
    await queued(service, conv)
    await worker.claim("ws1")
    clock.advance(61)
    outcome = await worker.run_one("ws1")
    assert (outcome.status, outcome.error_code) == ("failed", "interrupted")


async def test_run_forever_drains_and_survives_errors(files_db, tmp_path):
    conv = await files_db.conversation()
    service, worker, sandbox, _, _ = make(files_db, tmp_path, FakeMinerU("# ok"))
    await queued(service, conv)
    stop = asyncio.Event()
    spaces = iter([["bad-space!"], ["ws1"], ["ws1"]])

    def workspaces():
        try:
            return next(spaces)
        except StopIteration:
            stop.set()
            return []

    await asyncio.wait_for(worker.run_forever(workspaces, stop, idle_seconds=0.01), timeout=5)
    assert sandbox.inputs[("ws1", conv.id)] == {"报告.pdf": PDF, "报告.md": b"# ok"}


async def test_legacy_rows_restage_only_the_markdown(files_db, tmp_path):
    """迁移 0013 之前的 PDF、Word、PPT 只放了 Markdown（迁移后 parsed_name 等于 input_name），重放时照旧只放它。"""
    conv = await files_db.conversation()
    service, worker, sandbox, _, _ = make(files_db, tmp_path, FakeMinerU("# 旧"))
    item = await queued(service, conv)
    await worker.run_one("ws1")
    async with files_db.sessionmaker() as session, session.begin():
        await set_workspace(session, "ws1")
        await session.execute(update(Attachment).where(Attachment.id == item.id)
                              .values(input_name="报告.md", parsed_name="报告.md"))
    sandbox.inputs.clear()
    assert await service.restage("ws1", conv.id) == ()
    assert sandbox.inputs[("ws1", conv.id)] == {"报告.md": "# 旧".encode()}
