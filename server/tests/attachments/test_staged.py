"""文件先有、对话后建：输入框里一选文件就暂存在本人名下并开始解析，发消息时认领进会话、放进沙箱；移除与到期清理。"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI

from knowwork_server.attachments import ExpireJob, ParseJob, build_attachments, build_router
from knowwork_server.attachments.mineru import MinerUOptions
from knowwork_server.attachments.turns import TurnAttachments
from knowwork_server.conversations.service import AttachmentUnavailable
from knowwork_server.jobs import JobRunner
from knowwork_server.sessionfiles import local_session_stores
from knowwork_server.skills.blobstore import sha256_hex
from knowwork_server.skills.errors import BlobIntegrityError

from ..fake_parsers import EnvParsers
from ..fake_sandbox import FakeSandbox
from ..fakes import token_actor
from ..office_samples import PDF
from .test_attachments import CONFIGURED
from .test_parse_job import FakeMinerU, Fixed

pytestmark = pytest.mark.anyio
BASE = "/api/workspaces/default/attachments"
ALICE, BOB = "a" * 48, "b" * 48


class Clock:
    def __init__(self):
        self.now = datetime.now(UTC) + timedelta(seconds=5)

    def __call__(self):
        return self.now


class Env:
    """一套附件服务：alice、bob 两个人的接口客户端，一个跑解析与到期清理的 runner。"""

    def __init__(self, files_db, tmp_path, mineru=None):
        # PDF 当扫描件走 MinerU 桩，测的是暂存、认领与放置；直接提取那条路在 test_parse_job 里测
        self.sandbox, self.stores, self.clock = FakeSandbox(extracted=b"\f"), local_session_stores(tmp_path), Clock()
        self.attachments = build_attachments(files_db.sessionmaker, self.stores, self.sandbox, CONFIGURED,
                                             parsers=EnvParsers(CONFIGURED))
        self.service = self.attachments.service
        self.runner = JobRunner(files_db.sessionmaker, clock=self.clock)
        self.mineru = mineru or FakeMinerU("# 合同\n付款 30 天")
        ParseJob(self.service, self.stores, self.sandbox, Fixed(self.mineru), MinerUOptions(),
                 CONFIGURED).register(self.runner)
        ExpireJob(self.service).register(self.runner)
        self.turns = TurnAttachments(self.service)
        self.alice = self._client(files_db, ALICE, "alice")
        self.bob = self._client(files_db, BOB, "bob")

    def _client(self, files_db, token, user):
        app = FastAPI()
        app.include_router(build_router(self.service, files_db.lookup, token_actor(token, user, "ws1")))
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://public",
                                 headers={"authorization": f"Bearer {token}"})

    async def stage(self, *files, client=None):
        response = await (client or self.alice).post(BASE, files=[("files", (n, d)) for n, d in files])
        assert response.status_code == 200
        return response.json()

    async def states(self, *ids, client=None):
        return (await (client or self.alice).get(BASE, params={"ids": ",".join(ids)})).json()["items"]

    async def aclose(self):
        await self.alice.aclose()
        await self.bob.aclose()
        await self.attachments.aclose()


@pytest.fixture
async def env(files_db, tmp_path):
    made = Env(files_db, tmp_path)
    yield made
    await made.aclose()


async def test_staged_files_wait_under_the_user_until_sent(env):
    body = await env.stage(("合同.pdf", PDF), ("说明.txt", b"ok"), ("a.exe", b"MZ"))
    pdf, note = body["items"]
    assert [(i["filename"], i["status"], i["parseStatus"]) for i in body["items"]] == [
        ("合同.pdf", "staged", "parsing"), ("说明.txt", "staged", None)]
    assert [(f["filename"], f["code"]) for f in body["failures"]] == [("a.exe", "unsupported_format")]
    assert env.sandbox.inputs == {}, "还没有会话，不进任何沙箱"
    assert [i["id"] for i in await env.states(pdf["id"], note["id"], "att_nope")] == [pdf["id"], note["id"]]
    assert await env.states(pdf["id"], client=env.bob) == [], "别人看不到"


async def test_parsed_while_staged_goes_into_the_sandbox_with_the_original_on_send(env, files_db):
    [pdf] = (await env.stage(("合同.pdf", PDF)))["items"]
    assert (await env.runner.run_once("ws1")).status == "succeeded"
    assert env.sandbox.inputs == {}, "解析完只存下，等发消息"
    [state] = await env.states(pdf["id"])
    assert (state["status"], state["parseStatus"]) == ("staged", "ready")
    conv = await files_db.conversation()
    [turn] = await env.turns.for_turn(conv, [pdf["id"]])
    assert env.sandbox.inputs[("ws1", conv.id)] == {"合同.pdf": PDF, "合同.md": "# 合同\n付款 30 天".encode()}
    assert (turn.path, turn.extracted_path, turn.extraction_pending) == (
        "/workspace/input/合同.pdf", "/workspace/input/合同.md", False)
    assert [(v.status, v.parse_status) for v in await env.service.list(conv)] == [("ready", "ready")]


async def test_sent_before_parsing_finishes_the_job_puts_the_markdown_in_later(env, files_db):
    [pdf] = (await env.stage(("合同.pdf", PDF)))["items"]
    conv = await files_db.conversation()
    [turn] = await env.turns.for_turn(conv, [pdf["id"]])
    assert turn.extraction_pending and env.sandbox.inputs[("ws1", conv.id)] == {"合同.pdf": PDF}
    assert (await env.runner.run_once("ws1")).status == "succeeded"
    assert env.sandbox.inputs[("ws1", conv.id)]["合同.md"] == "# 合同\n付款 30 天".encode(), "解析任务放进认领它的会话"


async def test_names_in_the_sandbox_avoid_files_already_in_the_conversation(env, files_db):
    conv = await files_db.conversation()
    direct = await env.alice.post(f"/api/workspaces/default/conversations/{conv.id}/attachments",
                                  files=[("files", ("合同.pdf", PDF))])
    assert direct.json()["items"][0]["status"] == "ready", "按会话上传的直接放进这个会话"
    [again] = (await env.stage(("合同.pdf", PDF)))["items"]
    [turn] = await env.turns.for_turn(conv, [again["id"]])
    assert (turn.path, turn.extracted_path) == ("/workspace/input/合同 (2).pdf", "/workspace/input/合同 (2).md")


async def test_only_my_unsent_files_can_be_claimed(env, files_db):
    [pdf] = (await env.stage(("合同.pdf", PDF)))["items"]
    mine, other = await files_db.conversation(), await files_db.conversation()
    theirs = await files_db.conversation(user_id="bob")
    with pytest.raises(AttachmentUnavailable):
        await env.turns.for_turn(theirs, [pdf["id"]])
    await env.turns.for_turn(mine, [pdf["id"]])
    with pytest.raises(AttachmentUnavailable):
        await env.turns.for_turn(other, [pdf["id"]])  # 已经发进别的会话了
    assert len(await env.turns.for_turn(mine, [pdf["id"]])) == 1, "同一个会话里再带一次照用"
    with pytest.raises(AttachmentUnavailable):
        await env.turns.for_turn(mine, ["att_nope"])


async def test_removing_an_unsent_file_cancels_parsing_and_deletes_it(env, files_db):
    [pdf, kept] = (await env.stage(("合同.pdf", PDF), ("留着.pdf", PDF)))["items"]
    assert (await env.bob.delete(f"{BASE}/{pdf['id']}")).status_code == 404, "别人的删不了"
    assert (await env.alice.delete(f"{BASE}/{pdf['id']}")).status_code == 204
    assert (await env.alice.delete(f"{BASE}/{pdf['id']}")).status_code == 404
    assert [i["id"] for i in await env.states(pdf["id"], kept["id"])] == [kept["id"]]
    with pytest.raises(BlobIntegrityError):
        await env.stores("ws1", pdf["id"]).get(sha256_hex(PDF), max_bytes=1024)
    assert (await env.runner.run_once("ws1")).status == "succeeded"
    assert env.mineru.calls == ["留着.pdf"], "移除的那个不再解析"
    conv = await files_db.conversation()
    await env.turns.for_turn(conv, [kept["id"]])
    sent = await env.alice.delete(f"{BASE}/{kept['id']}")
    assert sent.status_code == 409 and sent.json()["detail"]["code"] == "attachment_sent"


async def test_files_never_sent_are_deleted_when_they_expire(env, files_db):
    [left, sent] = (await env.stage(("没发.txt", b"left"), ("发了.txt", b"sent")))["items"]
    conv = await files_db.conversation()
    await env.turns.for_turn(conv, [sent["id"]])
    assert await env.runner.run_once("ws1") is None, "还没到期"
    env.clock.now += timedelta(hours=CONFIGURED.attachment_staged_ttl_hours, seconds=1)
    assert (await env.runner.run_once("ws1")).status == "succeeded"
    assert (await env.runner.run_once("ws1")).status == "succeeded"
    assert [i["id"] for i in await env.states(left["id"], sent["id"])] == [sent["id"]]
    with pytest.raises(BlobIntegrityError):
        await env.stores("ws1", left["id"]).get(sha256_hex(b"left"), max_bytes=100)
    assert await env.stores("ws1", sent["id"]).get(sha256_hex(b"sent"), max_bytes=100) == b"sent"


async def test_small_files_go_into_the_turn_whole_and_big_ones_get_an_outline(env, files_db):
    """照 Claude 的做法：放得下的整篇放进这一轮，模型不用读文件；放不下的给字数和目录（行号），按需分段读。"""
    big = "\n".join(["# 采购文件", "## 第一章 邀请函", "正文" * 6000, "## 第二章 须知", "正文" * 6000])
    small, large = (await env.stage(("说明.txt", "合同签订后 30 天内付清".encode()), ("采购.md", big.encode())))["items"]
    conv = await files_db.conversation()
    first, second = await env.turns.for_turn(conv, [small["id"], large["id"]])
    assert (first.text, first.text_chars, first.outline) == ("合同签订后 30 天内付清", 13, ())
    assert second.text is None and second.text_chars == len(big)
    assert [(e.line, e.text) for e in second.outline] == [(1, "# 采购文件"), (2, "## 第一章 邀请函"), (4, "## 第二章 须知")]


async def test_the_whole_text_budget_is_shared_by_the_turn(env, files_db):
    """一轮合计也有上限：前面的附件把预算用掉了，后面放得下的也只给目录。"""
    text = ("这是一段正文。" * 2200).encode()  # 每份 15400 字，单个放得下，三份超过一轮的合计
    ids = [i["id"] for i in (await env.stage(("一.txt", text), ("二.txt", text), ("三.txt", text)))["items"]]
    conv = await files_db.conversation()
    turns = await env.turns.for_turn(conv, ids)
    assert [t.text is not None for t in turns] == [True, True, False]


async def test_long_pdf_without_headings_is_outlined_by_page(env, files_db):
    env.sandbox.extracted = b"\f".join(f"第 {n} 页的正文。".encode() * 100 for n in range(1, 31)) + b"\f"
    [pdf] = (await env.stage(("报告.pdf", PDF)))["items"]
    assert (await env.runner.run_once("ws1")).status == "succeeded"
    conv = await files_db.conversation()
    [turn] = await env.turns.for_turn(conv, [pdf["id"]])
    assert turn.text is None and turn.text_pages == 30 and len(turn.outline) == 30
    assert turn.outline[1].text.startswith("第 2 页：第 2 页的正文。")
    assert env.mineru.calls == [], "有文字层的 PDF 直接提取，不经 MinerU"


async def test_nothing_to_read_yet_while_still_parsing(env, files_db):
    [pdf] = (await env.stage(("报告.pdf", PDF)))["items"]
    conv = await files_db.conversation()
    [turn] = await env.turns.for_turn(conv, [pdf["id"]])
    assert turn.extraction_pending and turn.text_chars is None and turn.outline == ()


async def test_scanned_pdf_still_tells_the_model_how_many_pages_it_has(env, files_db):
    """扫描件识别出的 Markdown 没有页码标记，页数取提取时数出来的：模型知道 2 页就是全部，不再自己去转原件。"""
    env.sandbox.extracted = b"\f\f"
    [pdf] = (await env.stage(("承诺书.pdf", PDF)))["items"]
    assert (await env.runner.run_once("ws1")).status == "succeeded"
    conv = await files_db.conversation()
    [turn] = await env.turns.for_turn(conv, [pdf["id"]])
    assert (turn.text, turn.text_pages) == ("# 合同\n付款 30 天", 2)
    assert env.mineru.calls, "扫描件走了 MinerU"
