"""附件导读任务：放不进对话的长附件按段并发写要点、汇总成全文摘要，存成导读放在原件旁边；发消息时一起放进沙箱。"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from knowwork_server.attachments import ExpireJob, IncomingFile, ParseJob, build_attachments
from knowwork_server.attachments.mineru import MinerUOptions
from knowwork_server.attachments.turns import TurnAttachments
from knowwork_server.jobs import JobRunner
from knowwork_server.models.client import ModelCallError
from knowwork_server.sessionfiles import local_session_stores

from ..fake_parsers import EnvParsers
from ..fake_sandbox import FakeSandbox
from ..office_samples import PDF
from .test_attachments import CONFIGURED
from .test_parse_job import FakeMinerU, Fixed

pytestmark = pytest.mark.anyio
LONG = "\n".join([f"## 第{n}章 条款\n" + "合同正文第一句。" * 600 for n in range(1, 6)])   # 约 2.4 万字、5 章


class Clock:
    def __init__(self):
        self.now = datetime.now(UTC) + timedelta(seconds=5)

    def __call__(self):
        return self.now


class FakeModels:
    """按提示词回话：各段要点回「第 n 段要点」，压缩与全文摘要回固定的话；记下同时在跑的最多几个。"""

    def __init__(self, fail_sections: frozenset[int] = frozenset()):
        self.calls: list[str] = []
        self.fail_sections = fail_sections
        self.running = 0
        self.peak = 0

    async def chat(self, workspace_id, alias, messages, *, max_tokens=1024, temperature=0.2, background=True):
        self.running += 1
        self.peak = max(self.peak, self.running)
        await asyncio.sleep(0)
        self.running -= 1
        user = messages[-1]["content"]
        self.calls.append(user[:40])
        if "这是第 " in user:
            index = int(user.split("这是第 ")[1].split(" 段")[0])
            if index in self.fail_sections:
                raise ModelCallError("model_unavailable", "模型服务暂时不可用", retryable=True)
            return f"第 {index} 段要点：付款 30 天。"
        return "这是一份采购合同，共五章。"


def make(files_db, tmp_path, models, mineru=None):
    sandbox, stores, clock = FakeSandbox(), local_session_stores(tmp_path), Clock()
    attachments = build_attachments(files_db.sessionmaker, stores, sandbox, CONFIGURED, parsers=EnvParsers(CONFIGURED))
    runner = JobRunner(files_db.sessionmaker, clock=clock)
    ParseJob(attachments.service, stores, sandbox, Fixed(mineru or FakeMinerU("x")), MinerUOptions(),
             CONFIGURED).register(runner)
    attachments.digest_job(models).register(runner)
    ExpireJob(attachments.service).register(runner)
    return attachments.service, runner, sandbox


async def run_all(runner):
    while (outcome := await runner.run_once("ws1")) is not None:
        assert outcome.status == "succeeded", outcome


async def test_long_text_gets_a_digest_that_goes_into_the_sandbox_on_send(files_db, tmp_path):
    models = FakeModels()
    service, runner, sandbox = make(files_db, tmp_path, models)
    [item] = (await service.stage("ws1", "alice", [IncomingFile("采购.md", LONG.encode())])).items
    assert (item.digest_name, item.digest_status) == ("采购.导读.md", "digesting")
    await run_all(runner)
    [state] = await service.states("ws1", "alice", [item.id])
    assert (state.digest_status, state.digest_done, state.digest_total) == ("ready", 5, 5)
    conv = await files_db.conversation()
    [turn] = await TurnAttachments(service).for_turn(conv, [item.id])
    assert (turn.digest_path, turn.digest_pending, turn.text) == ("/workspace/input/采购.导读.md", False, None)
    digest = sandbox.inputs[("ws1", conv.id)]["采购.导读.md"].decode()
    assert digest.startswith("# 《采购.md》导读") and "按行号读 采购.md" in digest
    assert "## 全文摘要\n\n这是一份采购合同，共五章。" in digest
    assert "### 1. 第1章 条款（第 1–" in digest and "第 4 段要点：付款 30 天。" in digest


async def test_long_pdf_is_digested_after_extraction_and_sent_early_it_is_placed_later(files_db, tmp_path):
    models = FakeModels()
    service, runner, sandbox = make(files_db, tmp_path, models)
    sandbox.extracted = b"\f".join(f"第 {n} 页的正文，足够长。".encode() * 800 for n in range(1, 4)) + b"\f"  # 约 2.9 万字
    [item] = (await service.stage("ws1", "alice", [IncomingFile("年报.pdf", PDF)])).items
    assert item.digest_status is None, "文档要等提取完才知道多长"
    assert (await runner.run_once("ws1")).status == "succeeded"   # 提取文字，接着排导读
    conv = await files_db.conversation()
    [turn] = await TurnAttachments(service).for_turn(conv, [item.id])
    assert (turn.digest_path, turn.digest_pending) == ("/workspace/input/年报.导读.md", True)
    await run_all(runner)
    assert sandbox.inputs[("ws1", conv.id)]["年报.导读.md"].startswith("# 《年报.pdf》导读".encode()), "发出去以后做完的由任务放进会话"


async def test_failed_sections_are_marked_and_small_files_get_no_digest(files_db, tmp_path):
    models = FakeModels(fail_sections=frozenset({2}))
    service, runner, sandbox = make(files_db, tmp_path, models)
    small, long = (await service.stage("ws1", "alice", [IncomingFile("短.txt", "付款 30 天".encode()),
                                                         IncomingFile("长.md", LONG.encode())])).items
    assert small.digest_status is None
    await run_all(runner)
    conv = await files_db.conversation()
    await TurnAttachments(service).for_turn(conv, [long.id])
    digest = sandbox.inputs[("ws1", conv.id)]["长.导读.md"].decode()
    assert "这一段没能生成要点" in digest and "第 1 段要点" in digest
    assert sum("这是第 2 段" in call for call in models.calls) == 2, "失败的段重试了一次"


async def test_sections_run_in_parallel_under_the_cap(files_db, tmp_path):
    models = FakeModels()
    service, runner, _ = make(files_db, tmp_path, models)
    many = "\n".join(f"## 第{n}章\n" + "正文。" * 3000 for n in range(1, 13))   # 12 章，每章约 9000 字
    await service.stage("ws1", "alice", [IncomingFile("长.md", many.encode())])
    await run_all(runner)
    assert 1 < models.peak <= CONFIGURED.attachment_digest_concurrency


async def test_every_section_failing_fails_the_digest_but_not_the_attachment(files_db, tmp_path):
    models = FakeModels(fail_sections=frozenset(range(1, 10)))
    service, runner, _ = make(files_db, tmp_path, models)
    [item] = (await service.stage("ws1", "alice", [IncomingFile("长.md", LONG.encode())])).items
    outcome = await runner.run_once("ws1")
    assert (outcome.status, outcome.error_code) == ("retry", "digest_failed")
    [state] = await service.states("ws1", "alice", [item.id])
    assert state.status == "staged", "原件照样能发"


async def test_same_named_long_files_sent_before_extraction_get_their_own_digests(files_db, tmp_path):
    """导读的名字认领时就定下、和会话里的文件错开：两份同名文档都赶在提取完之前发出去，导读也不会互相覆盖。"""
    models = FakeModels()
    service, runner, sandbox = make(files_db, tmp_path, models)
    sandbox.extracted = b"\f".join(f"第 {n} 页的正文，足够长。".encode() * 800 for n in range(1, 4)) + b"\f"
    first, second = (await service.stage("ws1", "alice", [IncomingFile("年报.pdf", PDF),
                                                           IncomingFile("年报.pdf", PDF)])).items
    conv = await files_db.conversation()
    await TurnAttachments(service).for_turn(conv, [first.id])
    await TurnAttachments(service).for_turn(conv, [second.id])
    await run_all(runner)
    placed = sandbox.inputs[("ws1", conv.id)]
    assert "按行号读 年报.md" in placed["年报.导读.md"].decode()
    assert "按行号读 年报 (2).md" in placed["年报.导读 (2).md"].decode()


async def test_data_files_get_no_digest(files_db, tmp_path):
    """CSV、JSON 是数据，分段写要点没意义；问到了模型写代码去算。"""
    service, runner, _ = make(files_db, tmp_path, FakeModels())
    rows = ("日期,金额\n" + "2026-11-01,100\n" * 3000).encode()
    [item] = (await service.stage("ws1", "alice", [IncomingFile("流水.csv", rows)])).items
    assert (item.digest_name, item.digest_status) == (None, None)
    assert await runner.run_once("ws1") is None
