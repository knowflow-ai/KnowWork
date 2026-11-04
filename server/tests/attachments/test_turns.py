"""本轮消息带的附件：只认本会话、原件已就绪的；换成交给宿主的文件说明。

用户传完附件马上发消息时解析多半还在跑：短暂等它出结果（有上限），等到了文件说明带上解析稿路径——
没有它模型面对目录里「来历不明」的 .md 只会自己再从 PDF 抽一遍文本（实测发生过）。失败或超限按原件继续。
"""

import asyncio

import pytest
from sqlalchemy import update

from knowwork_server.access.rls import set_workspace
from knowwork_server.attachments import AttachmentSettings
from knowwork_server.attachments.models import Attachment
from knowwork_server.attachments.turns import TurnAttachments
from knowwork_server.conversations.service import AttachmentUnavailable
from knowwork_server.sandbox.contract import SandboxError
from knowwork_server.skills.blobstore import sha256_hex

from ..office_samples import PDF
from .test_attachments import post, setup

pytestmark = pytest.mark.anyio

NOWAIT = AttachmentSettings(mineru_base_url="http://mineru", attachment_parse_wait_seconds=0)


async def settle(files_db, attachments, att_id, seconds, **mark):
    """模拟 worker 收尾：写解析状态（要过行级安全，与生产同路径）。"""
    async def later():
        await asyncio.sleep(seconds)
        async with files_db.sessionmaker() as session:
            await set_workspace(session, "ws1")
            await session.execute(update(Attachment).where(Attachment.id == att_id).values(**mark))
            await session.commit()
    return asyncio.create_task(later())


async def test_ready_attachments_become_turn_files(files_db, tmp_path):
    conv = await files_db.conversation()
    other = await files_db.conversation()
    http, attachments, sandbox, _ = setup(files_db, tmp_path, NOWAIT)
    sandbox.fail_put = {"坏.txt": SandboxError("沙箱暂时不可用")}
    async with http:
        mine = (await post(http, conv.id, ("报告.pdf", PDF), ("坏.txt", b"x"))).json()["items"]
        theirs = (await post(http, other.id, ("别人的.txt", b"y"))).json()["items"]
    pdf, broken = mine
    turns = TurnAttachments(attachments.service, NOWAIT)
    [item] = await turns.for_turn(conv, [pdf["id"]])
    assert (item.name, item.path, item.bytes, item.sha256) == ("报告.pdf", "/workspace/input/报告.pdf", len(PDF),
                                                               sha256_hex(PDF))
    assert (item.extracted_path, item.parsing) == ("/workspace/input/报告.md", True), \
        "等待关闭时解析还在跑：给预期路径并标记解析中，宿主据此告知模型"
    for ids in ([broken["id"]], [theirs[0]["id"]], ["att_missing"]):
        with pytest.raises(AttachmentUnavailable):
            await turns.for_turn(conv, ids)
    await attachments.aclose()


async def test_waits_for_the_parse_and_hands_over_the_markdown(files_db, tmp_path):
    """发消息时解析还在跑：等它 ready，文件说明带上解析稿路径（宿主据此告诉模型长文档优先读 .md）。"""
    conv = await files_db.conversation()
    http, attachments, _, _ = setup(files_db, tmp_path)
    async with http:
        [pdf] = (await post(http, conv.id, ("报告.pdf", PDF))).json()["items"]
    waiting = AttachmentSettings(mineru_base_url="http://mineru", attachment_parse_wait_seconds=5)
    stop = await settle(files_db, attachments, pdf["id"], 0.2, parse_status="ready")
    [item] = await TurnAttachments(attachments.service, waiting).for_turn(conv, [pdf["id"]])
    await stop
    assert item.extracted_path == "/workspace/input/报告.md" and item.parsing is False, \
        "解析就位：说明路径带预期语义的文案，不带解析中标记"
    await attachments.aclose()


async def test_failed_parse_falls_back_to_the_original(files_db, tmp_path):
    """解析失败（文件问题）：不再等待，按原件继续，失败原因用户在附件上看得到。"""
    conv = await files_db.conversation()
    http, attachments, _, _ = setup(files_db, tmp_path)
    async with http:
        [pdf] = (await post(http, conv.id, ("报告.pdf", PDF))).json()["items"]
    waiting = AttachmentSettings(mineru_base_url="http://mineru", attachment_parse_wait_seconds=5)
    stop = await settle(files_db, attachments, pdf["id"], 0.05, parse_status="failed", error_code="file_corrupt",
                        error_message="文档解析失败，文件可能已损坏或加密")
    [item] = await TurnAttachments(attachments.service, waiting).for_turn(conv, [pdf["id"]])
    await stop
    assert item.extracted_path is None and item.parsing is False, "解析失败：不提示解析中，模型按原件继续"
    await attachments.aclose()


async def test_wait_gives_up_on_the_deadline(files_db, tmp_path):
    """一直解析不完：等到上限就放行，但告知模型解析仍在进行与预期路径，由它决定等或绕。"""
    conv = await files_db.conversation()
    http, attachments, _, _ = setup(files_db, tmp_path)
    async with http:
        [pdf] = (await post(http, conv.id, ("报告.pdf", PDF))).json()["items"]
    waiting = AttachmentSettings(mineru_base_url="http://mineru", attachment_parse_wait_seconds=0.1)
    [item] = await TurnAttachments(attachments.service, waiting).for_turn(conv, [pdf["id"]])
    assert item.extracted_path == "/workspace/input/报告.md" and item.parsing is True
    await attachments.aclose()
