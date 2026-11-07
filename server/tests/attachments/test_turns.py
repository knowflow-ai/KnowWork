"""本轮消息带的附件：只认本会话、原件已就绪的；换成交给宿主的文件说明。"""

from dataclasses import replace

import pytest

from knowwork_server.attachments.service import AttachmentView
from knowwork_server.attachments.turns import TurnAttachments, turn_attachment
from knowwork_server.conversations.service import AttachmentUnavailable
from knowwork_server.sandbox.contract import SandboxError
from knowwork_server.skills.blobstore import sha256_hex

from ..office_samples import PDF
from .test_attachments import post, setup

pytestmark = pytest.mark.anyio


async def test_ready_attachments_become_turn_files(files_db, tmp_path):
    conv = await files_db.conversation()
    other = await files_db.conversation()
    http, attachments, sandbox, _ = setup(files_db, tmp_path)
    sandbox.fail_put = {"坏.txt": SandboxError("沙箱暂时不可用")}
    async with http:
        mine = (await post(http, conv.id, ("报告.pdf", PDF), ("坏.txt", b"x"))).json()["items"]
        theirs = (await post(http, other.id, ("别人的.txt", b"y"))).json()["items"]
    pdf, broken = mine
    turns = TurnAttachments(attachments.service)
    [item] = await turns.for_turn(conv, [pdf["id"]])
    assert (item.name, item.path, item.bytes, item.sha256) == ("报告.pdf", "/workspace/input/报告.pdf", len(PDF),
                                                               sha256_hex(PDF))
    assert (item.extracted_path, item.extraction_pending) == ("/workspace/input/报告.md", True), \
        "还在解析：给出解析好以后的位置，并说明现在还没有"
    for ids in ([broken["id"]], [theirs[0]["id"]], ["att_missing"]):
        with pytest.raises(AttachmentUnavailable):
            await turns.for_turn(conv, ids)
    await attachments.aclose()


def _view(**changes) -> AttachmentView:
    base = AttachmentView(id="att_1", filename="报告.pdf", input_name="报告.pdf", kind="pdf", size=3, status="ready",
                          error_code=None, error_message=None, created_at=None, digest="a" * 64,
                          parsed_name="报告.md", parse_status="ready")
    return replace(base, **changes)


@pytest.mark.parametrize(("changes", "extracted", "pending"), [
    ({}, "/workspace/input/报告.md", False),
    ({"parse_status": "parsing"}, "/workspace/input/报告.md", True),
    ({"parse_status": "failed"}, None, False),
    ({"parsed_name": None, "parse_status": None}, None, False),
    # 迁移 0013 之前的旧行只放了 Markdown：原件就是那份 Markdown，不另给
    ({"input_name": "报告.md"}, None, False),
])
def test_turn_file_points_at_the_markdown_only_when_it_is_or_will_be_there(changes, extracted, pending):
    item = turn_attachment(_view(**changes))
    assert (item.extracted_path, item.extraction_pending) == (extracted, pending)
