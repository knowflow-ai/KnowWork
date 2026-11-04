"""附件类型判定（扩展名 + 魔数）与 MinerU 4.1 客户端（MockTransport 当桩，不连真实服务）。"""

import io
import json
import zipfile

import httpx
import pytest

from knowwork_server.attachments.mineru import MinerUClient, MinerUError, MinerUOptions, _unzip
from knowwork_server.attachments.settings import AttachmentSettings
from knowwork_server.attachments.types import AttachmentRejected, Kind, detect

from ..office_samples import DOCX, PDF, PPTX, ooxml, xlsx

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(("name", "data", "kind"), [
    ("a.pdf", PDF, Kind.PDF), ("a.PDF", b"junk" + PDF, Kind.PDF), ("a.docx", DOCX, Kind.DOCX),
    ("a.pptx", PPTX, Kind.PPTX), ("a.xlsx", xlsx([[1]]), Kind.XLSX), ("a.md", "# 标题".encode(), Kind.TEXT),
    ("a.txt", "中文".encode("gb18030"), Kind.TEXT), ("a.csv", b"a,b\n1,2", Kind.TEXT),
    ("a.json", b'{"k": 1}', Kind.TEXT), ("a.markdown", b"x", Kind.TEXT),
])
def test_detect_accepts(name, data, kind):
    assert detect(name, data).kind is kind


@pytest.mark.parametrize(("name", "data", "code"), [
    ("a.exe", b"MZ", "unsupported_format"), ("noext", b"x", "unsupported_format"),
    ("a.doc", b"\xd0\xcf\x11\xe0", "unsupported_format"), ("a.pdf", b"", "empty_file"),
    ("a.pdf", DOCX, "type_mismatch"), ("a.docx", PDF, "type_mismatch"), ("a.docx", PPTX, "type_mismatch"),
    ("a.xlsx", DOCX, "type_mismatch"), ("a.docx", b"PK\x03\x04broken", "file_corrupt"),
    ("a.docx", ooxml("word").replace(b"[Content_Types].xml", b"[Content_Typez].xml"), "file_corrupt"),
    ("a.txt", PDF, "type_mismatch"), ("a.csv", b"a\x00b", "type_mismatch"), ("a.md", b"\x89PNG....", "type_mismatch"),
    ("a.json", b"{bad", "file_corrupt"), ("a.txt", b"\xff\xfe\xfd\x81\x30", "file_corrupt"),
])
def test_detect_rejects(name, data, code):
    with pytest.raises(AttachmentRejected) as caught:
        detect(name, data)
    assert caught.value.code == code and caught.value.message


def test_settings_validate_mineru_url_and_tier(monkeypatch):
    monkeypatch.setenv("KNOWWORK_MINERU_BASE_URL", "http://mineru:8000/")
    settings = AttachmentSettings()
    assert settings.mineru_base_url == "http://mineru:8000" and settings.mineru_configured
    assert not AttachmentSettings(mineru_base_url="").mineru_configured
    with pytest.raises(ValueError):
        AttachmentSettings(mineru_base_url="ftp://x")
    assert AttachmentSettings(mineru_tier=" FLASH ").mineru_tier == "flash", "档位大小写与空格不敏感"
    assert AttachmentSettings(mineru_tier="").mineru_tier == "auto"
    with pytest.raises(ValueError):
        AttachmentSettings(mineru_tier="hybrid-http-client")  # 4.1 的档位没有旧解析模式这些值


class MinerUStub:
    """按路径回放的 MinerU 桩，记录请求。"""

    def __init__(self, routes):
        self.routes = {key: list(value) for key, value in routes.items()}
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        queue = self.routes.get((request.method, request.url.path))
        if queue is None:  # request_id 这类动态路径按前缀匹配（键以 * 结尾）
            for (method, path), candidate in self.routes.items():
                if method == request.method and path.endswith("*") and request.url.path.startswith(path[:-1]):
                    queue = candidate
                    break
        if not queue:
            return httpx.Response(404)
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        status, body = item
        return httpx.Response(status, content=body if isinstance(body, bytes) else json.dumps(body).encode())


def client(stub: MinerUStub, **options) -> MinerUClient:
    async def no_sleep(_):
        return None

    headers = options.pop("headers", None)
    http = httpx.AsyncClient(base_url="http://mineru", transport=httpx.MockTransport(stub.handler), headers=headers)
    return MinerUClient(http, MinerUOptions(**options), sleep=no_sleep)


def result_zip(markdown="# 解析结果", middle=None, images=None, extra=True) -> bytes:
    """按 4.1 的结果打包：markdown.md 必有（给 None 就是缺它），middle_json.json、images/ 看情况，
    还有用不上的 structured_content.json。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        if markdown is not None:
            archive.writestr("markdown.md", markdown)
        if middle is not None:
            archive.writestr("middle_json.json", json.dumps(middle) if not isinstance(middle, str) else middle)
        for name, content in (images or {}).items():
            archive.writestr(f"images/{name}", content)
        if extra:
            archive.writestr("structured_content.json", "[]")
    return buffer.getvalue()


RECEIPT = {"task_id": "t1", "files": [{"file_id": "f1", "file_name": "a.pdf",
                                      "upload": {"url": "http://mineru/v1/tasks/t1/uploads/f1", "headers": {}}}]}
DONE = {"status": "completed", "files": [{"file_id": "f1", "status": "completed",
                                         "result_url": "http://mineru/v1/files/f1/content"}]}


def async_flow(routes: dict, *, receipt=None, result=None) -> dict:
    """标准异步流程的路由：提交 → 上传 → 轮询（先 running 后 completed）→ 下载结果。"""
    routes.setdefault(("POST", "/v1/tasks"), [(200, receipt or RECEIPT)])
    routes[("PUT", "/v1/tasks/t1/uploads/f1")] = [(200, {"task_id": "t1", "file_id": "f1", "status": "pending"})]
    routes[("GET", "/v1/tasks/t1")] = [(200, {"status": "running"}), (200, DONE)]
    routes[("GET", "/v1/files/f1/content")] = [(200, result or result_zip())]
    return routes


async def test_async_task_flow():
    stub = MinerUStub(async_flow({}))
    assert await client(stub).parse("合同.pdf", PDF, "application/pdf") == "# 解析结果"
    submit, upload, download = stub.requests[0], stub.requests[1], stub.requests[-1]
    manifest = json.loads(submit.content)
    assert (manifest["files"][0]["file_name"], manifest["files"][0]["bytes"]) == ("合同.pdf", len(PDF))
    assert manifest["output_formats"] == ["default"] and "tier" not in manifest, "档位没配就不发，让 MinerU 自己推荐"
    assert upload.content == PDF and upload.method == "PUT", "文件字节走回执给的上传地址"
    assert download.url.path == "/v1/files/f1/content"


async def test_forced_tier_is_sent():
    stub = MinerUStub(async_flow({}))
    await client(stub, tier="flash").parse("a.pdf", PDF, "application/pdf")
    assert json.loads(stub.requests[0].content)["tier"] == "flash"


async def test_cache_hit_skips_the_upload():
    """文件命中 MinerU 的源缓存时回执没有 upload：直接等解析结果。"""
    stub = MinerUStub({("POST", "/v1/tasks"): [(200, {"task_id": "t1", "files": [{"file_id": "f1"}]})],
                       ("GET", "/v1/tasks/t1"): [(200, DONE)],
                       ("GET", "/v1/files/f1/content"): [(200, result_zip())]})
    assert await client(stub).parse("a.pdf", PDF, "application/pdf") == "# 解析结果"
    assert [request.method for request in stub.requests] == ["POST", "GET", "GET"]


async def test_falls_back_to_file_parse():
    """只部署了同步解析服务（file-parse-server）时：multipart 传单文件，成功直接回 ZIP。"""
    stub = MinerUStub({("POST", "/v1/tasks"): [(404, b"")],
                       ("POST", "/v1/file_parse"): [(200, result_zip())]})
    assert await client(stub).parse("a.pdf", PDF, "application/pdf") == "# 解析结果"
    body = stub.requests[1].content
    assert b'name="file"' in body and b'output_formats' in body and b'request_id' in body


async def test_sync_202_keeps_waiting_by_request_id():
    """wait_timeout 内没解析完：服务回 202 和 request_id，接着按它轮询到终态再取结果。"""
    stub = MinerUStub({("POST", "/v1/tasks"): [(404, b"")],
                       ("POST", "/v1/file_parse"): [(202, {"status": "running"})],
                       ("GET", "/v1/file_parse/*"): [(200, {"status": "completed",
                                                          "result_url": "http://mineru/v1/files/x/content"})],
                       ("GET", "/v1/files/x/content"): [(200, result_zip())]})
    assert await client(stub).parse("a.pdf", PDF, "application/pdf") == "# 解析结果"
    polls = [request for request in stub.requests if request.url.path.startswith("/v1/file_parse/")]
    assert len(polls) == 1 and polls[0].method == "GET", "轮询的路径带着提交时的 request_id"


async def test_sync_wait_timeout_stays_inside_the_request_timeout():
    """同步接口的 wait_timeout 不能超过 http 客户端自己的请求超时：连接先被掐断的话，202 恢复路径走不到。"""
    stub = MinerUStub({("POST", "/v1/tasks"): [(404, b"")],
                       ("POST", "/v1/file_parse"): [(200, result_zip())]})
    assert await client(stub, request_timeout_seconds=30).parse("a.pdf", PDF, "application/pdf") == "# 解析结果"
    assert b'name="wait_timeout"\r\n\r\n25' in stub.requests[1].content, "30 秒请求超时减去几秒余量"


async def test_old_mineru_is_reported_not_guessed():
    """3.x（/tasks、/file_parse）与 4.0（/v1/uploads、/v1/parse/jobs）的接口都没有：报版本不支持，不猜旧协议。"""
    stub = MinerUStub({})
    with pytest.raises(MinerUError) as caught:
        await client(stub).parse("a.pdf", PDF, "application/pdf")
    assert caught.value.code == "parser_version_unsupported" and not caught.value.retryable


@pytest.mark.parametrize(("status", "code", "retryable"), [
    (415, "unsupported_format", False), (400, "parser_protocol", False), (422, "parser_protocol", False),
    (500, "service_unavailable", True), (503, "service_unavailable", True), (429, "service_unavailable", True),
    (401, "parser_protocol", False),
])
async def test_submit_status_mapping(status, code, retryable):
    """提交清单阶段还没有文件字节：400/413/422 是清单/参数没被接受（协议问题），不冒充「文件损坏」；
    415 是按扩展名拒了格式，归「格式不支持」。"""
    stub = MinerUStub({("POST", "/v1/tasks"): [(status, b"internal /var/x")]})
    with pytest.raises(MinerUError) as caught:
        await client(stub).parse("a.pdf", PDF, "application/pdf")
    assert (caught.value.code, caught.value.retryable) == (code, retryable)
    assert "/var/x" not in caught.value.message


@pytest.mark.parametrize(("entry", "code", "retryable"), [
    ({"status": "failed", "error": {"message": "PDF is encrypted, password required"}}, "file_corrupt", False),
    ({"status": "failed", "error": {"message": "Device string must not be empty"}}, "parser_misconfigured", True),
    ({"status": "failed"}, "parse_failed", True),
])
async def test_task_failure_is_classified_by_its_message(entry, code, retryable):
    """W9：任务失败不一律当成文件坏了，看不出是文件问题的按服务问题重试；4.1 的错误是 {error: {message}} 对象。"""
    failed = MinerUStub({("POST", "/v1/tasks"): [(200, RECEIPT)],
                         ("PUT", "/v1/tasks/t1/uploads/f1"): [(200, b"")],
                         ("GET", "/v1/tasks/t1"): [(200, {"status": "failed", "files": [dict(entry, file_id="f1")]})]})
    with pytest.raises(MinerUError) as caught:
        await client(failed).parse("a.pdf", PDF, "application/pdf")
    assert (caught.value.code, caught.value.retryable) == (code, retryable)
    assert "Device" not in caught.value.message, "原始错误只进日志"


async def test_canceled_task_is_retryable():
    lost = MinerUStub({("POST", "/v1/tasks"): [(200, RECEIPT)],
                       ("PUT", "/v1/tasks/t1/uploads/f1"): [(200, b"")],
                       ("GET", "/v1/tasks/t1"): [(200, {"status": "canceled"})]})
    with pytest.raises(MinerUError) as caught:
        await client(lost).parse("a.pdf", PDF, "application/pdf")
    assert caught.value.retryable


async def test_lost_task_is_retryable():
    lost = MinerUStub({("POST", "/v1/tasks"): [(200, RECEIPT)],
                       ("PUT", "/v1/tasks/t1/uploads/f1"): [(200, b"")],
                       ("GET", "/v1/tasks/t1"): [(404, b"")]})
    with pytest.raises(MinerUError) as caught:
        await client(lost).parse("a.pdf", PDF, "application/pdf")
    assert caught.value.retryable


async def test_expired_upload_link_is_retryable():
    stub = MinerUStub({("POST", "/v1/tasks"): [(200, RECEIPT)],
                       ("PUT", "/v1/tasks/t1/uploads/f1"): [(410, {"error": {"code": "upload_expired"}})]})
    with pytest.raises(MinerUError) as caught:
        await client(stub).parse("a.pdf", PDF, "application/pdf")
    assert caught.value.retryable


async def test_malformed_upload_headers_are_ignored():
    """回执的 upload.headers 形态不对（字符串）就当没有额外头，上传照做，不炸成内部错误。"""
    receipt = {"task_id": "t1", "files": [{"file_id": "f1", "upload": {
        "url": "http://mineru/v1/tasks/t1/uploads/f1", "headers": "nope"}}]}
    stub = MinerUStub(async_flow({}, receipt=receipt))
    assert await client(stub).parse("a.pdf", PDF, "application/pdf") == "# 解析结果"


@pytest.mark.parametrize(("method", "path"), [("PUT", "/v1/tasks/t1/uploads/f1"), ("GET", "/v1/files/f1/content")])
async def test_lost_upload_or_result_is_retryable(method, path):
    """上传记录、结果文件被清了（404）都按「服务侧东西没了」整次重来，不是协议错误。"""
    stub = MinerUStub({("POST", "/v1/tasks"): [(200, RECEIPT)], ("PUT", "/v1/tasks/t1/uploads/f1"): [(200, b"")],
                       ("GET", "/v1/tasks/t1"): [(200, DONE)], (method, path): [(404, b"")]})
    with pytest.raises(MinerUError) as caught:
        await client(stub).parse("a.pdf", PDF, "application/pdf")
    assert caught.value.code == "service_unavailable" and caught.value.retryable


async def test_poll_deadline_is_a_timeout():
    stub = MinerUStub({("POST", "/v1/tasks"): [(200, RECEIPT)], ("PUT", "/v1/tasks/t1/uploads/f1"): [(200, b"")],
                       ("GET", "/v1/tasks/t1"): [(200, {"status": "running"})]})
    ticks = iter(range(0, 10_000, 100))

    async def no_sleep(_):
        return None

    http = httpx.AsyncClient(base_url="http://mineru", transport=httpx.MockTransport(stub.handler))
    mineru = MinerUClient(http, MinerUOptions(parse_timeout_seconds=250), sleep=no_sleep, clock=lambda: next(ticks))
    with pytest.raises(MinerUError) as caught:
        await mineru.parse("a.pdf", PDF, "application/pdf")
    assert caught.value.code == "timeout" and caught.value.retryable


@pytest.mark.parametrize("error", [httpx.ReadTimeout("slow"), httpx.ConnectError("refused 10.0.0.1")])
async def test_transport_errors_are_retryable(error):
    stub = MinerUStub({("POST", "/v1/tasks"): [error]})
    with pytest.raises(MinerUError) as caught:
        await client(stub).parse("a.pdf", PDF, "application/pdf")
    assert caught.value.retryable and "10.0.0.1" not in caught.value.message


@pytest.mark.parametrize("payload", [{"task_id": ""}, {"task_id": "t1"}, {"task_id": "t1", "files": []},
                                     {"task_id": "t1", "files": ["x"]}, b"not json"])
async def test_malformed_submit(payload):
    stub = MinerUStub({("POST", "/v1/tasks"): [(200, payload)]})
    with pytest.raises(MinerUError) as caught:
        await client(stub).parse("a.pdf", PDF, "application/pdf")
    assert caught.value.code == "parser_protocol"


@pytest.mark.parametrize("result", [b"not a zip", result_zip(markdown=None, extra=False)])
async def test_malformed_results_are_protocol_errors(result):
    stub = MinerUStub(async_flow({}, result=result))
    with pytest.raises(MinerUError) as caught:
        await client(stub).parse("a.pdf", PDF, "application/pdf")
    assert caught.value.code == "parser_protocol"


async def test_bad_middle_json_is_a_protocol_error_for_layout_results():
    """middle_json.json 不合法只有要版面的路径（parse_full）才在乎；附件路径根本不读它。"""
    stub = MinerUStub(async_flow({}, result=result_zip(middle="not json")))
    with pytest.raises(MinerUError) as caught:
        await client(stub).parse_full("a.pdf", PDF, "application/pdf")
    assert caught.value.code == "parser_protocol"
    assert await client(MinerUStub(async_flow({}, result=result_zip(middle="not json")))).parse(
        "a.pdf", PDF, "application/pdf") == "# 解析结果"


async def test_completed_without_result_url_is_a_protocol_error():
    stub = MinerUStub({("POST", "/v1/tasks"): [(200, RECEIPT)], ("PUT", "/v1/tasks/t1/uploads/f1"): [(200, b"")],
                       ("GET", "/v1/tasks/t1"): [(200, {"status": "completed",
                                                       "files": [{"file_id": "f1", "status": "completed"}]})]})
    with pytest.raises(MinerUError) as caught:
        await client(stub).parse("a.pdf", PDF, "application/pdf")
    assert caught.value.code == "parser_protocol"


async def test_full_result_brings_layout_and_images():
    """知识库要 middle_json 与图片：ZIP 里 middle_json.json 是 JSON 文件，images/ 的名字去掉路径，
    用不上的 structured_content.json 忽略；包里套一层目录（如 output/名/）也能按名字找到。"""
    middle = {"pdf_info": [{"page_idx": 0, "para_blocks": []}]}
    png = b"\x89PNG\r\n"
    nested = io.BytesIO()
    with zipfile.ZipFile(nested, "w") as archive:
        archive.writestr("output/a.pdf/markdown.md", "# 标题")
        archive.writestr("output/a.pdf/middle_json.json", json.dumps(middle))
        archive.writestr("output/a.pdf/images/x.png", png)
    for zip_bytes in (result_zip("# 标题", middle, {"x.png": png}), nested.getvalue()):
        stub = MinerUStub(async_flow({}, result=zip_bytes))
        full = await client(stub, tier="standard").parse_full("a.pdf", PDF, "application/pdf")
        assert (full.markdown, full.middle, full.images) == ("# 标题", middle, {"x.png": png})


async def test_markdown_only_parse_leaves_layout_bytes_unread():
    """附件只要 Markdown：middle_json 与图片不从 ZIP 里解出来（传输省不掉，省的是解包与内存）。"""
    middle = {"pdf_info": []}
    zip_bytes = result_zip("# 解析结果", middle, {"x.png": b"png"})
    only_markdown = _unzip(zip_bytes, layout=False)
    assert only_markdown.markdown == "# 解析结果" and only_markdown.middle is None and only_markdown.images == {}
    full = _unzip(zip_bytes)
    assert full.middle == middle and full.images == {"x.png": b"png"}


@pytest.mark.parametrize(("status", "code", "retryable"), [
    (415, "unsupported_format", False), (400, "file_corrupt", False), (503, "service_unavailable", True),
])
async def test_sync_errors_are_mapped(status, code, retryable):
    stub = MinerUStub({("POST", "/v1/tasks"): [(405, b"")], ("POST", "/v1/file_parse"): [(status, b"")]})
    with pytest.raises(MinerUError) as caught:
        await client(stub).parse("a.pdf", PDF, "application/pdf")
    assert (caught.value.code, caught.value.retryable) == (code, retryable)


async def test_token_stays_on_mineru_and_skips_signed_urls():
    """令牌发给解析服务自己；回执/结果给的跨源签名地址（对象存储）不带令牌，原样使用。"""
    cross_receipt = {"task_id": "t1", "files": [{"file_id": "f1", "upload": {
        "url": "http://store:9000/put/f1", "headers": {"X-Up": "1"}}}]}
    cross_done = {"status": "completed", "files": [{"file_id": "f1", "status": "completed",
                                                    "result_url": "http://store:9000/signed/x"}]}
    stub = MinerUStub({("POST", "/v1/tasks"): [(200, cross_receipt)],
                       ("PUT", "/put/f1"): [(200, b"")],
                       ("GET", "/v1/tasks/t1"): [(200, cross_done)],
                       ("GET", "/signed/x"): [(200, result_zip())]})
    await client(stub, headers={"Authorization": "Bearer tok"}).parse("a.pdf", PDF, "application/pdf")
    upload, download = stub.requests[1], stub.requests[-1]
    assert upload.headers["X-Up"] == "1" and "authorization" not in upload.headers
    assert "authorization" not in download.headers
    same = MinerUStub({("POST", "/v1/tasks"): [(200, RECEIPT)], ("PUT", "/v1/tasks/t1/uploads/f1"): [(200, b"")],
                       ("GET", "/v1/tasks/t1"): [(200, DONE)],
                       ("GET", "/v1/files/f1/content"): [(200, result_zip())]})
    await client(same, headers={"Authorization": "Bearer tok"}).parse("a.pdf", PDF, "application/pdf")
    assert same.requests[-1].headers["authorization"] == "Bearer tok", "同源的结果地址带着令牌"


async def test_relative_urls_and_implicit_ports_count_as_same_origin():
    """本地部署给相对路径（带不带前导斜杠都是解析服务自己）或带默认端口的绝对地址时，都算解析服务自己：令牌照发。"""
    for upload_url in ("/v1/tasks/t1/uploads/f1", "v1/tasks/t1/uploads/f1"):
        local_receipt = {"task_id": "t1", "files": [{"file_id": "f1", "upload": {"url": upload_url}}]}
        stub = MinerUStub({("POST", "/v1/tasks"): [(200, local_receipt)],
                           ("PUT", "/v1/tasks/t1/uploads/f1"): [(200, b"")],
                           ("GET", "/v1/tasks/t1"): [(200, {"status": "completed", "files": [
                               {"file_id": "f1", "status": "completed",
                                "result_url": "http://mineru:80/v1/files/f1/content"}]})],
                           ("GET", "/v1/files/f1/content"): [(200, result_zip())]})
        await client(stub, headers={"Authorization": "Bearer tok"}).parse("a.pdf", PDF, "application/pdf")
        upload, download = stub.requests[1], stub.requests[-1]
        assert upload.headers["authorization"] == "Bearer tok" and download.headers["authorization"] == "Bearer tok", \
            "相对路径与显式默认端口都别把令牌剥掉"
