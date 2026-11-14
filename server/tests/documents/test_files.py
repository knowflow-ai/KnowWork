"""对话里的文件按路径读（方案 7.7.3：网页与桌面同一套）：回答里的文件链接、交付卡片在云端对话里都走这里。

- 路径照模型写的样子收：相对路径、/workspace/...、宿主路径 /sessions/<本会话>/...；越出工作目录的拒绝；
- 交付过的读收好的那份（沙箱回收了也在）；没交付过的从会话沙箱现读，不为读文件新建容器，沙箱不在时说明已随执行环境回收；
- 转 PDF：交付过的复用产物预览（转一次、缓存），没交付过的现转；PDF 原样；不能转的 415；
- 按当前用户核对对话归属；在用户电脑上执行的对话，文件不在服务端。
"""

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import update

from knowwork_server.access.rls import set_workspace
from knowwork_server.artifacts import build_artifacts
from knowwork_server.artifacts.models import Artifact
from knowwork_server.conversations.constants import DEVICE_SOURCE
from knowwork_server.documents import ConversationFiles, PreviewService, build_router
from knowwork_server.sessionfiles import local_session_stores

from ..fake_sandbox import FakeSandbox
from ..fakes import token_actor

CONV = "/api/workspaces/default/conversations"

pytestmark = pytest.mark.anyio
TOKEN = "t" * 48
AUTH = {"authorization": f"Bearer {TOKEN}"}


def setup(files_db, tmp_path, *, user="alice"):
    sandbox, stores = FakeSandbox(), local_session_stores(tmp_path)
    artifacts = build_artifacts(files_db.sessionmaker, stores, sandbox)
    previews = PreviewService(artifacts.service, sandbox)
    files = ConversationFiles(artifacts.service, sandbox, previews)
    app = FastAPI()
    app.include_router(build_router(previews, files_db.lookup, token_actor(TOKEN, user, "ws1"), files=files))
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://public")
    return http, artifacts, sandbox


async def deliver(artifacts, sandbox, conv, path, data):
    """模型写了文件并用 present 交付：回合结束时收进存储。"""
    sandbox.write_output("ws1", conv.id, path, data)
    await artifacts.collector.collect("ws1", conv.id, paths=[f"/sessions/{conv.id}/{path}"])


def content(conv, path):
    return {"url": f"{CONV}/{conv.id}/files/content", "params": {"path": path}}


def preview(conv, path):
    return {"url": f"{CONV}/{conv.id}/files/preview", "params": {"path": path}}


async def test_paths_as_the_model_writes_them(files_db, tmp_path):
    conv = await files_db.conversation()
    http, _, sandbox = setup(files_db, tmp_path)
    sandbox.write_output("ws1", conv.id, "out/报告.md", b"# report")
    async with http:
        for path in ("out/报告.md", "./out/报告.md", "/workspace/out/报告.md", f"/sessions/{conv.id}/out/报告.md"):
            response = await http.get(**content(conv, path), headers=AUTH)
            assert response.status_code == 200, path
            assert response.content == b"# report"
        outside = await http.get(**content(conv, "../etc/passwd"), headers=AUTH)
        other = await http.get(**content(conv, "/sessions/c_other/a.md"), headers=AUTH)
    assert outside.status_code == 400 and other.status_code == 400
    assert outside.json()["detail"] == "只能读这段对话工作目录里的文件"


async def test_delivered_files_stay_readable_after_the_sandbox_is_recycled(files_db, tmp_path):
    conv = await files_db.conversation()
    http, artifacts, sandbox = setup(files_db, tmp_path)
    await deliver(artifacts, sandbox, conv, "out/报告.md", b"# delivered")
    sandbox.write_output("ws1", conv.id, "draft.md", b"# not delivered")
    sandbox.recycle("ws1", conv.id)
    async with http:
        delivered = await http.get(**content(conv, "out/报告.md"), headers=AUTH)
        gone = await http.get(**content(conv, "draft.md"), headers=AUTH)
    assert delivered.status_code == 200 and delivered.content == b"# delivered"
    assert "attachment" in delivered.headers["content-disposition"]
    assert gone.status_code == 410
    assert gone.json()["detail"] == "文件已随执行环境回收，交付过的文件仍可在交付卡片里打开"


async def test_artifacts_delivered_before_paths_were_recorded_are_found_by_name(files_db, tmp_path):
    """早先收的产物没记路径（source_path 为空）：按文件名认出来，旧对话的交付卡片在沙箱回收后照样打得开。"""
    conv = await files_db.conversation()
    http, artifacts, sandbox = setup(files_db, tmp_path)
    await deliver(artifacts, sandbox, conv, "out/旧报告.md", b"# old")
    async with files_db.sessionmaker() as session, session.begin():
        await set_workspace(session, "ws1")
        await session.execute(update(Artifact).values(source_path=None))
    sandbox.recycle("ws1", conv.id)
    async with http:
        found = await http.get(**content(conv, f"/sessions/{conv.id}/out/旧报告.md"), headers=AUTH)
    assert found.status_code == 200 and found.content == b"# old"


async def test_missing_and_unreadable_files_are_explained(files_db, tmp_path):
    conv = await files_db.conversation()
    http, _, sandbox = setup(files_db, tmp_path)
    sandbox.write_output("ws1", conv.id, "a.md", b"x")
    async with http:
        missing = await http.get(**content(conv, "nope.md"), headers=AUTH)
    assert missing.status_code == 404 and missing.json()["detail"] == "找不到这个文件，可能已经移走或删掉了"


async def test_previews_reuse_delivered_conversions_and_convert_live_files(files_db, tmp_path):
    conv = await files_db.conversation()
    http, artifacts, sandbox = setup(files_db, tmp_path)
    await deliver(artifacts, sandbox, conv, "简报.docx", b"PK-docx")
    sandbox.write_output("ws1", conv.id, "草稿.pptx", b"PK-pptx")
    sandbox.write_output("ws1", conv.id, "原件.pdf", b"%PDF-1.4 original")
    sandbox.write_output("ws1", conv.id, "说明.md", b"# notes")
    async with http:
        first = await http.get(**preview(conv, "简报.docx"), headers=AUTH)
        again = await http.get(**preview(conv, "简报.docx"), headers=AUTH)
        live = await http.get(**preview(conv, "草稿.pptx"), headers=AUTH)
        pdf = await http.get(**preview(conv, "原件.pdf"), headers=AUTH)
        unsupported = await http.get(**preview(conv, "说明.md"), headers=AUTH)
    assert first.status_code == 200 and first.content.startswith(b"%PDF-") and again.content == first.content
    assert live.status_code == 200 and live.headers["content-type"] == "application/pdf"
    assert pdf.content == b"%PDF-1.4 original"
    assert unsupported.status_code == 415
    assert sandbox.conversions == [(conv.id, "docx"), (conv.id, "pptx")], "交付过的只转一次，现读的现转"


async def test_only_the_owner_reads_and_device_conversations_have_no_server_files(files_db, tmp_path):
    conv = await files_db.conversation()
    device = await files_db.conversation(source=DEVICE_SOURCE)
    _, _, sandbox = setup(files_db, tmp_path)
    sandbox.write_output("ws1", conv.id, "a.md", b"x")
    bob, *_ = setup(files_db, tmp_path, user="bob")
    alice, *_ = setup(files_db, tmp_path)
    async with bob, alice:
        other = await bob.get(**content(conv, "a.md"), headers=AUTH)
        anonymous = await bob.get(**content(conv, "a.md"))
        local = await alice.get(**content(device, "a.md"), headers=AUTH)
    assert other.status_code == 404 and anonymous.status_code == 401
    assert local.status_code == 409 and local.json()["detail"] == "这段对话在创建它的电脑上执行，文件在那台电脑上"
