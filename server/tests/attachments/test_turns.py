"""本轮消息带的附件：只认本会话、原件已就绪的；换成交给宿主的文件说明。"""

import pytest

from knowwork_server.attachments.turns import TurnAttachments
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
    assert item.extracted_path is None, "还没解析出来就不给 Markdown 路径"
    for ids in ([broken["id"]], [theirs[0]["id"]], ["att_missing"]):
        with pytest.raises(AttachmentUnavailable):
            await turns.for_turn(conv, ids)
    await attachments.aclose()
