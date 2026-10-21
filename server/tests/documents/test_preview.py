"""产物预览：Word、PPT 在沙箱里转 PDF 只转一次、结果缓存；PDF 原样返回；其他类型 415；按当前用户鉴权；
转换失败如实说明；缓存丢了重新转；会话删除时预览 PDF 一并删除。"""

import hashlib

import httpx
import pytest
from fastapi import FastAPI

from knowwork_server.artifacts import build_artifacts
from knowwork_server.documents import PreviewService, build_router
from knowwork_server.sandbox.contract import SandboxError
from knowwork_server.sessionfiles import local_session_stores
from knowwork_server.skills.errors import BlobIntegrityError

from ..fake_sandbox import FakeSandbox
from ..fakes import token_actor

pytestmark = pytest.mark.anyio
TOKEN = "t" * 48
AUTH = {"authorization": f"Bearer {TOKEN}"}


def setup(files_db, tmp_path, *, user="alice"):
    sandbox, stores = FakeSandbox(), local_session_stores(tmp_path)
    artifacts = build_artifacts(files_db.sessionmaker, stores, sandbox)
    previews = PreviewService(artifacts.service, sandbox)
    app = FastAPI()
    app.include_router(build_router(previews, files_db.lookup, token_actor(TOKEN, user, "ws1")))
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://public")
    return http, artifacts, sandbox, stores


async def collect(artifacts, sandbox, conv, name, data):
    sandbox.write_output("ws1", conv.id, name, data)
    result = await artifacts.collector.collect("ws1", conv.id, paths=[f"/sessions/{conv.id}/{name}"])
    return result.artifacts[0]


async def test_office_files_convert_once_and_are_cached(files_db, tmp_path):
    conv = await files_db.conversation()
    http, artifacts, sandbox, _ = setup(files_db, tmp_path)
    doc = await collect(artifacts, sandbox, conv, "简报.docx", b"PK-docx-content")
    url = f"/api/conversations/{conv.id}/artifacts/{doc.id}/preview"
    async with http:
        first = await http.get(url, headers=AUTH)
        second = await http.get(url, headers=AUTH)
    assert first.status_code == 200 and first.content.startswith(b"%PDF-")
    assert first.headers["content-type"] == "application/pdf"
    assert first.headers["x-content-type-options"] == "nosniff" and "简报.pdf" in __import__("urllib.parse").parse.unquote(first.headers["content-disposition"])
    assert second.content == first.content
    assert sandbox.conversions == [(conv.id, "docx")], "第二次应当直接取缓存"


async def test_pdf_is_returned_as_is_and_other_types_are_415(files_db, tmp_path):
    conv = await files_db.conversation()
    http, artifacts, sandbox, _ = setup(files_db, tmp_path)
    pdf = await collect(artifacts, sandbox, conv, "原件.pdf", b"%PDF-1.4 original")
    notes = await collect(artifacts, sandbox, conv, "说明.md", b"# notes")
    async with http:
        raw = await http.get(f"/api/conversations/{conv.id}/artifacts/{pdf.id}/preview", headers=AUTH)
        unsupported = await http.get(f"/api/conversations/{conv.id}/artifacts/{notes.id}/preview", headers=AUTH)
        missing = await http.get(f"/api/conversations/{conv.id}/artifacts/art_none/preview", headers=AUTH)
    assert raw.content == b"%PDF-1.4 original"
    assert unsupported.status_code == 415
    assert missing.status_code == 404
    assert sandbox.conversions == []


async def test_only_the_owner_can_preview(files_db, tmp_path):
    conv = await files_db.conversation()
    _, artifacts, sandbox, _ = setup(files_db, tmp_path)
    doc = await collect(artifacts, sandbox, conv, "a.pptx", b"PK-pptx")
    bob, *_ = setup(files_db, tmp_path, user="bob")
    async with bob:
        response = await bob.get(f"/api/conversations/{conv.id}/artifacts/{doc.id}/preview", headers=AUTH)
        anonymous = await bob.get(f"/api/conversations/{conv.id}/artifacts/{doc.id}/preview")
    assert response.status_code == 404 and anonymous.status_code == 401
    assert sandbox.conversions == []


async def test_conversion_failure_is_explained_and_not_cached(files_db, tmp_path):
    conv = await files_db.conversation()
    http, artifacts, sandbox, _ = setup(files_db, tmp_path)
    doc = await collect(artifacts, sandbox, conv, "坏.docx", b"PK-bad")
    sandbox.fail_convert = SandboxError("文档转换失败（invalid-document）")
    url = f"/api/conversations/{conv.id}/artifacts/{doc.id}/preview"
    async with http:
        failed = await http.get(url, headers=AUTH)
        sandbox.fail_convert = None
        retried = await http.get(url, headers=AUTH)
    assert failed.status_code == 503 and "invalid-document" in failed.json()["detail"]
    assert retried.status_code == 200 and len(sandbox.conversions) == 2


async def test_lost_cache_is_rebuilt_and_erase_removes_preview(files_db, tmp_path):
    conv = await files_db.conversation()
    http, artifacts, sandbox, stores = setup(files_db, tmp_path)
    doc = await collect(artifacts, sandbox, conv, "a.docx", b"PK-a")
    url = f"/api/conversations/{conv.id}/artifacts/{doc.id}/preview"
    async with http:
        pdf = (await http.get(url, headers=AUTH)).content
        await stores("ws1", conv.id).delete(hashlib.sha256(pdf).hexdigest())
        again = await http.get(url, headers=AUTH)
    assert again.status_code == 200 and len(sandbox.conversions) == 2
    await artifacts.service.erase(conv)
    with pytest.raises(BlobIntegrityError):
        await stores("ws1", conv.id).get(hashlib.sha256(pdf).hexdigest(), max_bytes=1000)
