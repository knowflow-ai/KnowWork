"""产物：回合结束收模型 present 交付的文件、上限逐项告知、表格转义、下载实时鉴权、会话删除即不可下载。"""

import io
from urllib.parse import unquote

import httpx
import pytest
from fastapi import FastAPI
from openpyxl import load_workbook

from knowwork_server.artifacts import ArtifactLimits, build_artifacts, build_router
from knowwork_server.sandbox.contract import SandboxError
from knowwork_server.sessionfiles import local_session_stores

from ..fake_sandbox import FakeSandbox
from ..fakes import token_actor
from ..office_samples import xlsx

CONV = "/api/workspaces/default/conversations"

pytestmark = pytest.mark.anyio
TOKEN = "t" * 48
AUTH = {"authorization": f"Bearer {TOKEN}"}
LIMITS = ArtifactLimits(max_file_bytes=100, max_per_turn=3, max_session_bytes=250)


def setup(files_db, tmp_path, limits=LIMITS, *, user="alice", on_download=None, settle=None):
    sandbox, stores = FakeSandbox(), local_session_stores(tmp_path)
    artifacts = build_artifacts(files_db.sessionmaker, stores, sandbox, limits)
    app = FastAPI()
    app.include_router(build_router(artifacts.service, files_db.lookup, token_actor(TOKEN, user, "ws1"),
                                    on_download=on_download, settle=settle))
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://public")
    return http, artifacts, sandbox, stores


async def test_collect_and_download(files_db, tmp_path):
    conv = await files_db.conversation()
    downloads = []

    async def audit(who, artifact):
        downloads.append((who.user_id, artifact.filename))

    http, artifacts, sandbox, _ = setup(files_db, tmp_path, on_download=audit)
    sandbox.write_output("ws1", conv.id, "季度报告.md", "# 结论".encode())
    sandbox.write_output("ws1", conv.id, "page.html", b"<script>alert(1)</script>")
    result = await artifacts.collector.collect("ws1", conv.id, run_id="run_1", paths=sandbox.delivered("ws1", conv.id))
    assert result.notices == () and result.message is None
    assert [a.filename for a in result.artifacts] == ["page.html", "季度报告.md"]
    md = next(a for a in result.artifacts if a.filename == "季度报告.md")
    async with http:
        listed = (await http.get(f"{CONV}/{conv.id}/artifacts", headers=AUTH)).json()
        response = await http.get(f"{CONV}/{conv.id}/artifacts/{md.id}", headers=AUTH)
        html = await http.get(f"{CONV}/{conv.id}/artifacts/{result.artifacts[0].id}", headers=AUTH)
    assert [i["filename"] for i in listed["items"]] == ["page.html", "季度报告.md"]
    assert listed["items"][0]["runId"] == "run_1"
    assert response.content == "# 结论".encode()
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;") and unquote(disposition.split("UTF-8''")[1]) == "季度报告.md"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-type"].startswith("text/markdown")
    assert html.headers["content-type"] == "application/octet-stream"
    assert html.headers["content-disposition"].startswith("attachment;")
    assert "sandbox" in html.headers["content-security-policy"]
    assert downloads == [("alice", "季度报告.md"), ("alice", "page.html")]


async def test_keeps_the_description_given_when_presenting(files_db, tmp_path):
    """present 时给的说明跟着产物存下来，卡片上显示；收成一行、过长截断，没给就是 null。"""
    conv = await files_db.conversation()
    http, artifacts, sandbox, _ = setup(files_db, tmp_path)
    sandbox.write_output("ws1", conv.id, "图.png", b"png")
    sandbox.write_output("ws1", conv.id, "页.html", b"<p>")
    sandbox.write_output("ws1", conv.id, "长.md", b"#")
    paths = sandbox.delivered("ws1", conv.id)
    path_of = {path.rsplit("/", 1)[1]: path for path in paths}
    described = {path_of["图.png"]: "  四阶段流程\n加柱状图  ", path_of["页.html"]: "说" * 400}
    await artifacts.collector.collect("ws1", conv.id, paths=paths, descriptions=described)
    async with http:
        items = (await http.get(f"{CONV}/{conv.id}/artifacts", headers=AUTH)).json()["items"]
    by_name = {item["filename"]: item["description"] for item in items}
    assert by_name["图.png"] == "四阶段流程 加柱状图"
    assert by_name["页.html"] == "说" * 300
    assert by_name["长.md"] is None


async def test_list_waits_for_in_flight_collection_before_querying(files_db, tmp_path):
    """前端收到 run.completed 立即来列：列表先等这个会话正在收的产物落库，刚生成的文件这一轮就能看到。"""
    conv = await files_db.conversation()
    settled = []
    box = {}

    async def settle(conversation_id):
        settled.append(conversation_id)
        box["sandbox"].write_output("ws1", conv.id, "总结.docx", b"PK")
        await box["artifacts"].collector.collect("ws1", conv.id, run_id="run-2", paths=box["sandbox"].delivered("ws1", conv.id))

    http, artifacts, sandbox, _ = setup(files_db, tmp_path, settle=settle)
    box.update(sandbox=sandbox, artifacts=artifacts)
    async with http:
        listed = (await http.get(f"{CONV}/{conv.id}/artifacts", headers=AUTH)).json()
        other = await http.get(f"{CONV}/conv_unknown/artifacts", headers=AUTH)
    assert settled == [conv.id], "不是本人的会话不该触发等待"
    assert other.status_code == 404
    [item] = listed["items"]
    assert (item["filename"], item["runId"], item["size"]) == ("总结.docx", "run-2", 2)


async def test_unchanged_files_are_not_collected_twice(files_db, tmp_path):
    conv = await files_db.conversation()
    _, artifacts, sandbox, _ = setup(files_db, tmp_path)
    sandbox.write_output("ws1", conv.id, "a.txt", b"v1")
    assert len((await artifacts.collector.collect("ws1", conv.id, paths=sandbox.delivered("ws1", conv.id))).artifacts) == 1
    assert (await artifacts.collector.collect("ws1", conv.id, paths=sandbox.delivered("ws1", conv.id))).artifacts == ()
    sandbox.write_output("ws1", conv.id, "a.txt", b"v2")
    assert [a.size for a in (await artifacts.collector.collect("ws1", conv.id, paths=sandbox.delivered("ws1", conv.id))).artifacts] == [2]
    assert len(await artifacts.service.list(conv)) == 2


async def test_limits_are_reported_one_by_one(files_db, tmp_path):
    conv = await files_db.conversation()
    _, artifacts, sandbox, _ = setup(files_db, tmp_path)
    for name, size in (("a.txt", 90), ("b.txt", 90), ("big.txt", 101), ("c.txt", 10), ("d.txt", 10), ("e.txt", 80)):
        sandbox.write_output("ws1", conv.id, name, b"x" * size)
    sandbox.write_output("ws1", conv.id, "宏.xlsm", b"PK")
    result = await artifacts.collector.collect("ws1", conv.id, paths=sandbox.delivered("ws1", conv.id))
    assert [a.filename for a in result.artifacts] == ["a.txt", "b.txt", "c.txt"]
    assert [(n.filename, n.code) for n in result.notices] == [
        ("big.txt", "file_too_large"), ("d.txt", "turn_limit"), ("e.txt", "turn_limit"),
        ("宏.xlsm", "unsupported_spreadsheet")]
    assert result.message.startswith("有 4 个文件没有收为产物：\n- big.txt：超过单文件 0 MiB 上限")
    second = await artifacts.collector.collect("ws1", conv.id, paths=sandbox.delivered("ws1", conv.id))  # 下一轮：每轮计数重置，会话累计仍算
    assert [a.filename for a in second.artifacts] == ["d.txt"]
    assert ("e.txt", "session_quota") in [(n.filename, n.code) for n in second.notices]


async def test_spreadsheets_are_escaped_before_storage(files_db, tmp_path):
    conv = await files_db.conversation()
    limits = ArtifactLimits(max_file_bytes=1024 * 1024, max_per_turn=20, max_session_bytes=10 * 1024 * 1024)
    http, artifacts, sandbox, _ = setup(files_db, tmp_path, limits)
    sandbox.write_output("ws1", conv.id, "表.xlsx", xlsx([["=1+1", 2]]))
    sandbox.write_output("ws1", conv.id, "表.csv", b"=cmd,1\n")
    sandbox.write_output("ws1", conv.id, "坏.xlsx", b"PK\x03\x04bad")
    result = await artifacts.collector.collect("ws1", conv.id, paths=sandbox.delivered("ws1", conv.id))
    assert [(n.filename, n.code) for n in result.notices] == [("坏.xlsx", "spreadsheet_rejected")]
    by_name = {a.filename: a for a in result.artifacts}
    assert by_name["表.csv"].sanitized and by_name["表.xlsx"].sanitized
    async with http:
        csv = await http.get(f"{CONV}/{conv.id}/artifacts/{by_name['表.csv'].id}", headers=AUTH)
        sheet = await http.get(f"{CONV}/{conv.id}/artifacts/{by_name['表.xlsx'].id}", headers=AUTH)
    assert csv.content.startswith(b"'=cmd")
    cell = load_workbook(io.BytesIO(sheet.content)).active["A1"]
    assert cell.value == "=1+1" and cell.quotePrefix
    assert (await artifacts.collector.collect("ws1", conv.id, paths=sandbox.delivered("ws1", conv.id))).artifacts == ()  # 原始内容没变，不重复收


async def test_sandbox_problems_become_notices(files_db, tmp_path):
    conv = await files_db.conversation()
    _, artifacts, sandbox, _ = setup(files_db, tmp_path)
    sandbox.write_output("ws1", conv.id, "a.txt", b"x")
    sandbox.write_output("ws1", conv.id, "sub/b.txt", b"x")
    sandbox.write_output("ws1", conv.id, "c.txt", b"x" * 500)
    sandbox.fail_read = {"a.txt": SandboxError("读不到")}
    result = await artifacts.collector.collect("ws1", conv.id, paths=sandbox.delivered("ws1", conv.id))
    assert [(n.filename, n.code) for n in result.notices] == [("a.txt", "read_failed"), ("c.txt", "file_too_large")]
    assert [a.filename for a in result.artifacts] == ["b.txt"], "子目录里交付的文件按文件名收"


async def test_only_files_in_this_session_workspace_and_one_per_name(files_db, tmp_path):
    conv = await files_db.conversation()
    _, artifacts, sandbox, _ = setup(files_db, tmp_path)
    sandbox.write_output("ws1", conv.id, "a/r.txt", b"1")
    sandbox.write_output("ws1", conv.id, "b/r.txt", b"2")
    paths = [f"/sessions/{conv.id}/a/r.txt", f"/sessions/{conv.id}/a/r.txt", f"/sessions/{conv.id}/b/r.txt",
             "/sessions/other/x.txt", "/etc/passwd"]
    result = await artifacts.collector.collect("ws1", conv.id, paths=paths)
    assert [a.size for a in result.artifacts] == [1]
    assert [(n.filename, n.code) for n in result.notices] == [
        ("r.txt", "duplicate_name"), ("x.txt", "outside_workspace"), ("passwd", "outside_workspace")]
    assert sandbox.reads == ["a/r.txt"], "会话工作目录之外的路径不该去读"
    assert (await artifacts.collector.collect("ws1", conv.id, paths=[])).artifacts == ()


async def test_download_checks_owner_every_time_and_dies_with_conversation(files_db, tmp_path):
    conv = await files_db.conversation()
    http, artifacts, sandbox, stores = setup(files_db, tmp_path)
    bob_http, *_ = setup(files_db, tmp_path, user="bob")
    sandbox.write_output("ws1", conv.id, "a.txt", b"secret")
    [artifact] = (await artifacts.collector.collect("ws1", conv.id, paths=sandbox.delivered("ws1", conv.id))).artifacts
    url = f"{CONV}/{conv.id}/artifacts/{artifact.id}"
    async with http, bob_http:
        assert (await http.get(url, headers=AUTH)).status_code == 200
        assert (await bob_http.get(url, headers=AUTH)).status_code == 404
        assert (await http.get(url)).status_code == 401
        assert (await http.get(f"{CONV}/{conv.id}/artifacts/art_missing", headers=AUTH)).status_code == 404
        assert (await http.get(f"{CONV}/{conv.id}/artifacts/bad-id!", headers=AUTH)).status_code == 422
        await stores("ws1", conv.id).delete(__import__("hashlib").sha256(b"secret").hexdigest())
        assert (await http.get(url, headers=AUTH)).status_code == 410
        await artifacts.service.erase(conv)
        await files_db.delete_conversation(conv)
        assert (await http.get(url, headers=AUTH)).status_code == 404
    assert await artifacts.service.list(conv) == ()


async def test_erase_removes_objects(files_db, tmp_path):
    conv = await files_db.conversation()
    _, artifacts, sandbox, stores = setup(files_db, tmp_path)
    sandbox.write_output("ws1", conv.id, "a.txt", b"bye")
    [_artifact] = (await artifacts.collector.collect("ws1", conv.id, paths=sandbox.delivered("ws1", conv.id))).artifacts
    await artifacts.service.erase(conv)
    from knowwork_server.skills.errors import BlobIntegrityError
    with pytest.raises(BlobIntegrityError):
        await stores("ws1", conv.id).get(__import__("hashlib").sha256(b"bye").hexdigest(), max_bytes=10)
