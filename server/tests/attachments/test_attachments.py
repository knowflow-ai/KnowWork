"""附件上传接口与服务：类型判定、放进沙箱、逐个告知失败、行级安全、会话删除清理。"""

import httpx
import pytest
from fastapi import FastAPI

from knowwork_server.attachments import AttachmentSettings, build_attachments, build_router
from knowwork_server.jobs import JobRunner
from knowwork_server.sandbox.contract import SandboxError, SandboxTooLarge
from knowwork_server.sessionfiles import local_session_stores
from knowwork_server.skills.blobstore import sha256_hex
from knowwork_server.skills.errors import BlobIntegrityError

from ..fake_parsers import EnvParsers
from ..fake_sandbox import FakeSandbox
from ..fakes import token_actor
from ..office_samples import DOCX, PDF, xlsx

pytestmark = pytest.mark.anyio
TOKEN = "t" * 48
AUTH = {"authorization": f"Bearer {TOKEN}"}
CONFIGURED = AttachmentSettings(mineru_base_url="http://mineru", attachment_max_bytes=1024 * 1024,
                                attachment_max_files_per_request=3)


def setup(files_db, tmp_path, settings=CONFIGURED, *, user="alice", workspace="ws1"):
    sandbox = FakeSandbox()
    stores = local_session_stores(tmp_path)
    attachments = build_attachments(files_db.sessionmaker, stores, sandbox, settings, parsers=EnvParsers(settings))
    app = FastAPI()
    app.include_router(build_router(attachments.service, files_db.lookup, token_actor(TOKEN, user, workspace)))
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://public")
    return http, attachments, sandbox, stores


def parse_runner(files_db, attachments):
    runner = JobRunner(files_db.sessionmaker)
    attachments.parse_job.register(runner)
    return runner


async def post(http, cid, *files):
    return await http.post(f"/api/conversations/{cid}/attachments", headers=AUTH,
                           files=[("files", (name, data)) for name, data in files])


async def test_text_and_excel_go_straight_into_the_sandbox(files_db, tmp_path):
    conv = await files_db.conversation()
    http, attachments, sandbox, _ = setup(files_db, tmp_path)
    sheet = xlsx([["a", 1]])
    async with http:
        response = await post(http, conv.id, ("说明.md", "# 你好".encode()), ("数据.xlsx", sheet))
        listed = (await http.get(f"/api/conversations/{conv.id}/attachments", headers=AUTH)).json()
    assert response.status_code == 200
    body = response.json()
    assert body["failures"] == []
    assert [(i["filename"], i["status"], i["kind"]) for i in body["items"]] == [
        ("说明.md", "ready", "text"), ("数据.xlsx", "ready", "xlsx")]
    assert sandbox.inputs[("ws1", conv.id)] == {"说明.md": "# 你好".encode(), "数据.xlsx": sheet}
    assert [i["status"] for i in listed["items"]] == ["ready", "ready"]
    assert listed["items"][0]["createdAt"]
    await attachments.aclose()


async def test_pdf_without_mineru_is_placed_as_is(files_db, tmp_path):
    """没配文档解析也照收：原件原样放进沙箱（照官方 dsh-attachment），模型用沙箱里的工具自己读。"""
    conv = await files_db.conversation()
    http, attachments, sandbox, _ = setup(files_db, tmp_path, AttachmentSettings())
    async with http:
        body = (await post(http, conv.id, ("合同.pdf", PDF), ("a.txt", b"ok"))).json()
    assert body["failures"] == []
    assert [(i["inputName"], i["status"], i["parseStatus"]) for i in body["items"]] == [
        ("合同.pdf", "ready", None), ("a.txt", "ready", None)]
    assert sandbox.inputs[("ws1", conv.id)]["合同.pdf"] == PDF
    assert not await attachments.mineru.configured("ws1"), "没配 MinerU：只放原件，不排解析"


async def test_pdf_with_mineru_is_placed_and_queued_for_parsing(files_db, tmp_path):
    conv = await files_db.conversation()
    http, attachments, sandbox, _ = setup(files_db, tmp_path)
    async with http:
        body = (await post(http, conv.id, ("合同.pdf", PDF), ("合同.docx", DOCX))).json()
    assert [(i["inputName"], i["status"], i["parsedName"], i["parseStatus"]) for i in body["items"]] == [
        ("合同.pdf", "ready", "合同.md", "parsing"), ("合同.docx", "ready", "合同 (2).md", "parsing")]
    assert set(sandbox.inputs[("ws1", conv.id)]) == {"合同.pdf", "合同.docx"}, "原件先放进去，解析是附加的"
    claim = await parse_runner(files_db, attachments).claim("ws1")
    assert (claim.kind, claim.tries) == ("attachment.parse", 1), "解析任务和附件行一起排进了任务表"
    await attachments.aclose()


async def test_failures_are_listed_one_by_one(files_db, tmp_path):
    conv = await files_db.conversation()
    http, attachments, sandbox, _ = setup(files_db, tmp_path)
    sandbox.fail_put = {"b.txt": SandboxError("沙箱暂时不可用"), "c.csv": SandboxTooLarge("超过沙箱上限")}
    async with http:
        body = (await post(http, conv.id, ("a.exe", b"MZ"), ("b.txt", b"x"), ("c.csv", b"1,2"))).json()
        big = (await post(http, conv.id, ("big.txt", b"x" * (1024 * 1024 + 1)))).json()
    assert [(f["filename"], f["code"]) for f in body["failures"]] == [
        ("a.exe", "unsupported_format"), ("b.txt", "sandbox_unavailable"), ("c.csv", "sandbox_too_large")]
    assert body["failures"][1]["message"] == "沙箱暂时不可用"
    assert [i["status"] for i in body["items"]] == ["place_failed", "place_failed"]
    assert big["failures"] == [{"filename": "big.txt", "code": "too_large", "message": "文件超过 1 MiB 上限"}]
    await attachments.aclose()


async def test_request_level_limits_and_ownership(files_db, tmp_path):
    mine = await files_db.conversation()
    theirs = await files_db.conversation(user_id="bob")
    elsewhere = await files_db.conversation(workspace_id="ws2")
    http, attachments, _, _ = setup(files_db, tmp_path)
    async with http:
        too_many = await post(http, mine.id, *[(f"{i}.txt", b"x") for i in range(4)])
        other_user = await post(http, theirs.id, ("a.txt", b"x"))
        other_space = await http.get(f"/api/conversations/{elsewhere.id}/attachments", headers=AUTH)
        anonymous = await http.get(f"/api/conversations/{mine.id}/attachments")
    assert too_many.status_code == 400 and too_many.json()["detail"]["code"] == "too_many_files"
    assert other_user.status_code == 404 and other_space.status_code == 404 and anonymous.status_code == 401
    await attachments.aclose()


async def test_rls_hides_other_workspaces(files_db, tmp_path):
    conv = await files_db.conversation()
    _, attachments, _, _ = setup(files_db, tmp_path)
    await post(_client(files_db, tmp_path, attachments), conv.id, ("a.txt", b"x"))
    spoofed = type(conv)(id=conv.id, workspace_id="ws2", user_id="alice", title="")
    assert await attachments.service.list(spoofed) == ()
    assert len(await attachments.service.list(conv)) == 1


def _client(files_db, tmp_path, attachments):
    app = FastAPI()
    app.include_router(build_router(attachments.service, files_db.lookup, token_actor(TOKEN, "alice", "ws1")))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://public")


async def test_invalid_names_and_storage_failure(files_db, tmp_path):
    conv = await files_db.conversation()
    sandbox = FakeSandbox()

    class BrokenStore:
        async def put(self, data):
            raise BlobIntegrityError("disk full /var/lib")

    attachments = build_attachments(files_db.sessionmaker, lambda w, s: BrokenStore(), sandbox, CONFIGURED,
                                    parsers=EnvParsers(CONFIGURED))
    from knowwork_server.attachments import IncomingFile
    result = await attachments.service.upload(conv, [IncomingFile(".env", b"x"), IncomingFile("a.txt", b"x"),
                                                     IncomingFile(None, b"x")])
    assert [(f.filename, f.code) for f in result.failures] == [
        (".env", "invalid_name"), ("a.txt", "storage_unavailable"), ("（未命名文件）", "invalid_name")]
    assert "/var/lib" not in result.failures[1].message


async def test_restage_and_erase(files_db, tmp_path):
    conv = await files_db.conversation()
    http, attachments, sandbox, stores = setup(files_db, tmp_path)
    async with http:
        await post(http, conv.id, ("a.txt", b"hello"), ("b.pdf", PDF))
    sandbox.inputs.clear()  # 容器被回收，工作区丢了
    sandbox.fail_put = {"a.txt": SandboxError("沙箱暂时不可用")}
    failures = await attachments.service.restage("ws1", conv.id)
    assert [(f.filename, f.code) for f in failures] == [("a.txt", "sandbox_unavailable")]
    sandbox.fail_put = {}
    assert await attachments.service.restage("ws1", conv.id) == ()
    assert sandbox.inputs[("ws1", conv.id)] == {"a.txt": b"hello", "b.pdf": PDF}, "原件都重放；还没解析出的 Markdown 不放"
    assert [(v.status, v.parse_status) for v in await attachments.service.list(conv)] == [
        ("ready", None), ("ready", "parsing")]

    await attachments.service.erase(conv)
    assert await attachments.service.list(conv) == ()
    with pytest.raises(BlobIntegrityError):
        await stores("ws1", conv.id).get(sha256_hex(b"hello"), max_bytes=100)
    assert await parse_runner(files_db, attachments).claim("ws1") is None, "解析任务随会话删除一起取消"
    await attachments.aclose()


async def test_restage_reports_missing_content(files_db, tmp_path):
    conv = await files_db.conversation()
    http, attachments, _sandbox, stores = setup(files_db, tmp_path)
    async with http:
        await post(http, conv.id, ("a.txt", b"hello"))
    await stores("ws1", conv.id).delete(sha256_hex(b"hello"))
    failures = await attachments.service.restage("ws1", conv.id)
    assert [f.code for f in failures] == ["content_missing"]
    await attachments.aclose()
