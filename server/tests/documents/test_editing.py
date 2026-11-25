"""扩展编辑对话里的文件（documents/editing.py）：核对归属、读最新的一份、存盘成新的一版并写回会话沙箱。

- 只认本人的对话；在设备上执行的、工作目录外的、附件目录里的都拒绝，说明直接给人看；
- 存盘：同一路径多一行就是新的一版，读文件、交付卡片拿到的是最新的；和最新的一版一样不另存；改了又改回去也照样存；
- 会话沙箱还在就写回工作目录，回收了不为此新建；表格存储时照交付转义，写回沙箱的是原样。
"""

import hashlib

import pytest

from knowwork_server.artifacts import build_artifacts
from knowwork_server.artifacts.collector import ArtifactLimits
from knowwork_server.conversations.constants import DEVICE_SOURCE
from knowwork_server.documents import ConversationFiles, PreviewService
from knowwork_server.documents.editing import ConversationFileEditing, EditRefused
from knowwork_server.sessionfiles import local_session_stores

from ..fake_sandbox import FakeSandbox

pytestmark = pytest.mark.anyio


def setup(files_db, tmp_path, *, limits: ArtifactLimits | None = None, settle=None, inputs=None, claim_draft=None):
    sandbox, stores = FakeSandbox(), local_session_stores(tmp_path)
    artifacts = build_artifacts(files_db.sessionmaker, stores, sandbox)
    if limits is not None:
        artifacts.collector._limits = limits
    files = ConversationFiles(artifacts.service, sandbox, PreviewService(artifacts.service, sandbox), inputs=inputs)
    editing = ConversationFileEditing(files_db.lookup, files, artifacts.collector, sandbox, settle=settle,
                                      claim_draft=claim_draft)
    return editing, artifacts, sandbox, files


async def locate(editing, conv, path, *, user="alice"):
    return await editing.locate(workspace_id=conv.workspace_id, conversation_id=conv.id, user_id=user, path=path)


async def working_copy(editing, conv, path, *, user="alice"):
    return await editing.working_copy(workspace_id=conv.workspace_id, conversation_id=conv.id, user_id=user,
                                      path=path)


async def test_only_the_owner_edits_files_in_the_working_folder(files_db, tmp_path):
    conv = await files_db.conversation()
    editing, _, sandbox, _ = setup(files_db, tmp_path)
    sandbox.write_output("ws1", conv.id, "output/报告.docx", b"v1")
    found = await locate(editing, conv, f"/sessions/{conv.id}/output/报告.docx")
    assert (found.path.relative, found.relative, found.name) == ("output/报告.docx", "output/报告.docx", "报告.docx")
    assert await editing.read(found) == b"v1"

    for path, status in (("../etc/passwd", 400), ("input/客户.docx", 409), ("/workspace/input", 409)):
        with pytest.raises(EditRefused) as refused:
            await locate(editing, conv, path)
        assert refused.value.status == status, path
    with pytest.raises(EditRefused) as refused:
        await locate(editing, conv, "output/报告.docx", user="bob")
    assert refused.value.status == 404
    device = await files_db.conversation(source=DEVICE_SOURCE)
    with pytest.raises(EditRefused) as refused:
        await locate(editing, device, "a.docx")
    assert refused.value.status == 409
    missing = await locate(editing, conv, "output/没有.docx")
    with pytest.raises(EditRefused) as refused:
        await editing.read(missing)
    assert refused.value.status == 404


async def test_each_save_is_a_new_version_at_the_same_path(files_db, tmp_path):
    conv = await files_db.conversation()
    editing, artifacts, sandbox, files = setup(files_db, tmp_path)
    sandbox.write_output("ws1", conv.id, "output/报告.docx", b"v1")
    await artifacts.collector.collect("ws1", conv.id, paths=[f"/sessions/{conv.id}/output/报告.docx"],
                                      descriptions={f"/sessions/{conv.id}/output/报告.docx": "季度报告"})
    found = await locate(editing, conv, "output/报告.docx")

    unchanged = await editing.save(found, b"v1")
    assert not unchanged.changed and unchanged.artifact_id is None

    second = await editing.save(found, b"v2")
    assert second.changed and second.synced
    assert (await files.read(conv, files.locate(conv, "output/报告.docx"))).data == b"v2"
    assert sandbox.outputs[("ws1", conv.id)]["output/报告.docx"] == b"v2"

    # 改回第一版：只和最新的一版比，照样存成新的一版，最新的就是第一版的内容
    back = await editing.save(found, b"v1")
    assert back.changed
    assert (await files.read(conv, files.locate(conv, "output/报告.docx"))).data == b"v1"
    listed = await artifacts.service.list(conv)
    assert [item.description for item in listed] == ["季度报告"] * 3
    assert all(item.run_id is None for item in listed[1:])


async def test_saving_a_file_that_was_never_delivered_stores_its_first_version(files_db, tmp_path):
    conv = await files_db.conversation()
    editing, artifacts, sandbox, files = setup(files_db, tmp_path)
    sandbox.write_output("ws1", conv.id, "草稿.docx", b"draft")
    found = await locate(editing, conv, "草稿.docx")
    saved = await editing.save(found, b"edited")
    assert saved.changed
    sandbox.recycle("ws1", conv.id)  # 回收后读的是存好的那一版
    assert (await files.read(conv, files.locate(conv, "草稿.docx"))).data == b"edited"
    assert len(await artifacts.service.list(conv)) == 1


async def test_latest_digest_matches_what_read_gives_without_reading_the_whole_file(files_db, tmp_path):
    """最新一版的摘要（扩展比「编辑器里那份变没变」用）：和 read() 读到的内容的 sha256 一致，表格存的是转义后的也一样；
    交付过的直接用记下的摘要，不从存储里整份读出来。"""
    conv = await files_db.conversation()
    editing, artifacts, sandbox, _ = setup(files_db, tmp_path)
    sandbox.write_output("ws1", conv.id, "output/草稿.docx", b"live")
    live = await locate(editing, conv, "output/草稿.docx")
    assert await editing.latest_digest(live) == hashlib.sha256(b"live").hexdigest()

    sandbox.write_output("ws1", conv.id, "output/表.csv", b"=1+1,x\n")
    await artifacts.collector.collect("ws1", conv.id, paths=[f"/sessions/{conv.id}/output/表.csv"])
    table = await locate(editing, conv, "output/表.csv")
    stored = await editing.read(table)
    assert stored != b"=1+1,x\n", "表格存储时转义过"

    async def no_whole_reads(*args, **kwargs):
        raise AssertionError("交付过的文件不该整份读出来")

    artifacts.service.open = no_whole_reads
    assert await editing.latest_digest(table) == hashlib.sha256(stored).hexdigest()
    missing = await locate(editing, conv, "output/没有.docx")
    with pytest.raises(EditRefused) as refused:
        await editing.latest_digest(missing)
    assert refused.value.status == 404


async def test_an_unchanged_save_leaves_the_sandbox_alone(files_db, tmp_path):
    """和最新一版一样的保存：不另存，也不写回沙箱。不白搬一份，也不把模型在沙箱里接着改、还没交付的那份盖回去。"""
    conv = await files_db.conversation()
    editing, artifacts, sandbox, _ = setup(files_db, tmp_path)
    sandbox.write_output("ws1", conv.id, "output/报告.docx", b"v1")
    await artifacts.collector.collect("ws1", conv.id, paths=[f"/sessions/{conv.id}/output/报告.docx"])
    sandbox.write_output("ws1", conv.id, "output/报告.docx", b"model's newer draft")
    found = await locate(editing, conv, "output/报告.docx")
    saved = await editing.save(found, b"v1")
    assert not saved.changed and not saved.synced
    assert sandbox.outputs[("ws1", conv.id)]["output/报告.docx"] == b"model's newer draft"


async def test_recycled_sandbox_is_not_recreated_for_a_save(files_db, tmp_path):
    conv = await files_db.conversation()
    editing, _, sandbox, files = setup(files_db, tmp_path)
    sandbox.write_output("ws1", conv.id, "a.docx", b"v1")
    found = await locate(editing, conv, "a.docx")
    sandbox.recycle("ws1", conv.id)
    saved = await editing.save(found, b"v2")
    assert saved.changed and not saved.synced
    assert (await files.read(conv, files.locate(conv, "a.docx"))).data == b"v2"


async def test_read_waits_for_the_turns_deliverables_to_land(files_db, tmp_path):
    """一轮刚结束时产物在后台收：读之前等这段对话正在收的落库，拿到的才是 AI 这一轮交付的那版。"""
    conv = await files_db.conversation()
    pending: list[str] = []

    async def settle(conversation_id: str) -> None:
        await artifacts.collector.collect("ws1", conversation_id, paths=pending, descriptions={})

    editing, artifacts, sandbox, _ = setup(files_db, tmp_path, settle=settle)
    sandbox.write_output("ws1", conv.id, "a.docx", b"v1")
    await artifacts.collector.collect("ws1", conv.id, paths=[f"/sessions/{conv.id}/a.docx"], descriptions={})
    sandbox.write_output("ws1", conv.id, "a.docx", b"ai")
    pending.append(f"/sessions/{conv.id}/a.docx")
    assert await editing.read(await locate(editing, conv, "a.docx")) == b"ai"


async def test_an_attachment_is_edited_through_a_same_name_copy_in_the_working_folder(files_db, tmp_path):
    """附件只读：要编辑时在工作目录根上建同名副本，存成这个路径的一版并写回沙箱，原件不动。"""
    conv = await files_db.conversation()
    editing, artifacts, sandbox, files = setup(files_db, tmp_path)
    sandbox.write_output("ws1", conv.id, "input/北京众城简介 .docx", b"orig")
    for path in ("input/北京众城简介 .docx", "/workspace/input/北京众城简介 .docx"):
        copy = await working_copy(editing, conv, path)
        assert copy.relative == "北京众城简介 .docx", path
    assert await editing.read(copy) == b"orig"
    assert sandbox.outputs[("ws1", conv.id)]["北京众城简介 .docx"] == b"orig"
    assert len(await artifacts.service.list(conv)) == 1, "点两次只存一版"
    sandbox.recycle("ws1", conv.id)
    assert (await files.read(conv, files.locate(conv, "北京众城简介 .docx"))).data == b"orig", "存成了一版，回收后也在"


async def test_a_working_copy_that_already_exists_is_reused_not_overwritten(files_db, tmp_path):
    """第二次点开接着改上次那份：根上已有同名文件（人改过的、模型自己放的）就用它，不拿原件盖掉。"""
    conv = await files_db.conversation()
    editing, artifacts, sandbox, _ = setup(files_db, tmp_path)
    sandbox.write_output("ws1", conv.id, "input/合同.docx", b"orig")
    copy = await working_copy(editing, conv, "input/合同.docx")
    await editing.save(copy, b"edited")
    again = await working_copy(editing, conv, "input/合同.docx")
    assert again.relative == "合同.docx" and await editing.read(again) == b"edited"

    sandbox.write_output("ws1", conv.id, "input/报价.xlsx", b"orig")
    sandbox.write_output("ws1", conv.id, "报价.xlsx", b"model's copy")
    reused = await working_copy(editing, conv, "input/报价.xlsx")
    assert await editing.read(reused) == b"model's copy"
    assert len(await artifacts.service.list(conv)) == 2, "已有的不另存"


async def test_a_working_copy_is_made_from_the_stored_attachment_after_the_sandbox_is_recycled(files_db, tmp_path):
    """沙箱空闲回收后附件仍在附件存储里：照样能建副本（存成一版，等沙箱再起来时由模型那边读到）。"""
    conv = await files_db.conversation()

    async def inputs(conversation, name):
        return b"stored original" if (conversation.id, name) == (conv.id, "简介.docx") else None

    editing, _, sandbox, _ = setup(files_db, tmp_path, inputs=inputs)
    sandbox.recycle("ws1", conv.id)
    copy = await working_copy(editing, conv, "input/简介.docx")
    assert await editing.read(copy) == b"stored original"
    with pytest.raises(EditRefused) as refused:
        await working_copy(editing, conv, "input/不在了.docx")
    assert refused.value.status == 410


async def test_only_the_owners_attachments_get_working_copies(files_db, tmp_path):
    conv = await files_db.conversation()
    editing, _, sandbox, _ = setup(files_db, tmp_path)
    sandbox.write_output("ws1", conv.id, "input/a.docx", b"orig")
    sandbox.write_output("ws1", conv.id, "output/b.docx", b"x")
    for path, status in (("output/b.docx", 400), ("input", 400), ("/workspace/input", 400), ("../etc/passwd", 400),
                         ("input/没有.docx", 404)):
        with pytest.raises(EditRefused) as refused:
            await working_copy(editing, conv, path)
        assert refused.value.status == status, path
    with pytest.raises(EditRefused) as refused:
        await working_copy(editing, conv, "input/a.docx", user="bob")
    assert refused.value.status == 404
    device = await files_db.conversation(source=DEVICE_SOURCE)
    with pytest.raises(EditRefused) as refused:
        await working_copy(editing, device, "input/a.docx")
    assert refused.value.status == 409
    assert "a.docx" not in sandbox.outputs[("ws1", conv.id)], "拒绝的不建副本"


def docx_only(name: str) -> bool:
    return name.endswith(".docx")


async def draft_copy(editing, conv, attachment_id, *, user="alice"):
    return await editing.draft_copy(workspace_id=conv.workspace_id, conversation_id=conv.id, user_id=user,
                                    attachment_id=attachment_id, editable=docx_only)


async def test_a_draft_attachment_opens_through_a_working_copy_without_sending_a_message(files_db, tmp_path):
    """输入框里还没发出去的附件直接进编辑器：认领进对话（放进附件目录、名字和会话里已有的错开），再在根上建同名副本；
    之后点消息上的附件卡片打开的是同一份。"""
    conv = await files_db.conversation()
    claims: list[tuple[str, str]] = []

    async def claim_draft(conversation, attachment_id, editable):
        claims.append((conversation.id, attachment_id))
        if attachment_id != "att_1" or not editable("简报 (1).docx"):
            return None
        sandbox.write_output("ws1", conversation.id, "input/简报 (1).docx", b"orig")  # 认领时放进附件目录
        return "简报 (1).docx"

    editing, artifacts, sandbox, _ = setup(files_db, tmp_path, claim_draft=claim_draft)
    copy = await draft_copy(editing, conv, "att_1")
    assert copy.relative == "简报 (1).docx" and await editing.read(copy) == b"orig"
    await editing.save(copy, b"edited")
    again = await working_copy(editing, conv, "input/简报 (1).docx")
    assert again.relative == copy.relative and await editing.read(again) == b"edited", "消息上的卡片打开同一份"
    assert len(await artifacts.service.list(conv)) == 2

    with pytest.raises(EditRefused) as refused:
        await draft_copy(editing, conv, "att_别人的")
    assert refused.value.status == 404
    assert claims == [(conv.id, "att_1"), (conv.id, "att_别人的")]


async def test_draft_attachments_are_not_claimed_into_conversations_that_refuse_them(files_db, tmp_path):
    """对话不是本人的、在设备上执行的：认领之前就拒绝，附件留在输入框里；没接上认领的部署也拒绝。"""
    claimed: list[str] = []

    async def claim_draft(conversation, attachment_id, editable):
        claimed.append(conversation.id)
        return "a.docx"

    conv = await files_db.conversation()
    device = await files_db.conversation(source=DEVICE_SOURCE)
    editing, _, _, _ = setup(files_db, tmp_path, claim_draft=claim_draft)
    for target, user, status in ((conv, "bob", 404), (device, "alice", 409)):
        with pytest.raises(EditRefused) as refused:
            await draft_copy(editing, target, "att_1", user=user)
        assert refused.value.status == status
    assert claimed == []

    bare, _, _, _ = setup(files_db, tmp_path)
    with pytest.raises(EditRefused) as refused:
        await draft_copy(bare, conv, "att_1")
    assert refused.value.status == 404


async def test_limits_refuse_the_save_with_a_reason(files_db, tmp_path):
    conv = await files_db.conversation()
    editing, _, sandbox, _ = setup(files_db, tmp_path, limits=ArtifactLimits(max_file_bytes=4))
    sandbox.write_output("ws1", conv.id, "a.docx", b"v1")
    found = await locate(editing, conv, "a.docx")
    with pytest.raises(EditRefused) as refused:
        await editing.save(found, b"too large")
    assert refused.value.status == 413 and "上限" in str(refused.value)
