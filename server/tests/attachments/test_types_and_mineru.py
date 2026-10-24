"""附件类型判定（扩展名 + 魔数）与 MinerU 3.x 客户端（MockTransport 当桩，不连真实服务）。"""

import base64
import json

import httpx
import pytest

from knowwork_server.attachments.mineru import MinerUClient, MinerUError, MinerUOptions, usable
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


def test_settings_validate_mineru_url(monkeypatch):
    monkeypatch.setenv("KNOWWORK_MINERU_BASE_URL", "http://mineru:8000/")
    settings = AttachmentSettings()
    assert settings.mineru_base_url == "http://mineru:8000" and settings.mineru_configured
    assert not AttachmentSettings(mineru_base_url="").mineru_configured
    with pytest.raises(ValueError):
        AttachmentSettings(mineru_base_url="ftp://x")


class MinerUStub:
    """按路径回放的 MinerU 桩，记录请求。"""

    def __init__(self, routes):
        self.routes = {key: list(value) for key, value in routes.items()}
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        queue = self.routes.get((request.method, request.url.path))
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

    http = httpx.AsyncClient(base_url="http://mineru", transport=httpx.MockTransport(stub.handler))
    return MinerUClient(http, MinerUOptions(**options), sleep=no_sleep)


RESULT = {"results": {"a": {"md_content": "# 解析结果"}}}


async def test_async_task_flow():
    stub = MinerUStub({("POST", "/tasks"): [(200, {"task_id": "t1"})],
                       ("GET", "/tasks/t1"): [(200, {"status": "running"}), (200, {"status": "Completed"})],
                       ("GET", "/tasks/t1/result"): [(200, RESULT)]})
    assert await client(stub).parse("合同.pdf", PDF, "application/pdf") == "# 解析结果"
    submit = stub.requests[0]
    assert b'name="files"; filename="' in submit.content and b"return_md" in submit.content
    assert b'name="backend"\r\n\r\npipeline' in submit.content


async def test_falls_back_to_file_parse():
    stub = MinerUStub({("POST", "/tasks"): [(404, b"")], ("POST", "/file_parse"): [(200, RESULT)]})
    assert await client(stub).parse("a.pdf", PDF, "application/pdf") == "# 解析结果"


async def test_mineru_4_is_reported_not_guessed():
    stub = MinerUStub({})
    with pytest.raises(MinerUError) as caught:
        await client(stub).parse("a.pdf", PDF, "application/pdf")
    assert caught.value.code == "parser_version_unsupported" and not caught.value.retryable


@pytest.mark.parametrize(("status", "code", "retryable"), [
    (415, "unsupported_format", False), (400, "file_corrupt", False), (422, "file_corrupt", False),
    (500, "file_corrupt", False), (503, "service_unavailable", True), (429, "service_unavailable", True),
    (401, "parser_protocol", False),
])
async def test_submit_status_mapping(status, code, retryable):
    stub = MinerUStub({("POST", "/tasks"): [(status, b"internal /var/x")]})
    with pytest.raises(MinerUError) as caught:
        await client(stub).parse("a.pdf", PDF, "application/pdf")
    assert (caught.value.code, caught.value.retryable) == (code, retryable)
    assert "/var/x" not in caught.value.message


@pytest.mark.parametrize(("task", "code", "retryable"), [
    ({"status": "failed", "error": "PDF is encrypted, password required"}, "file_corrupt", False),
    ({"status": "failed", "error": "Device string must not be empty"}, "parser_misconfigured", True),  # 实测的配置错
    ({"status": "failed"}, "parse_failed", True),
])
async def test_task_failure_is_classified_by_its_message(task, code, retryable):
    """W9：任务失败不一律当成文件坏了，看不出是文件问题的按服务问题重试。"""
    failed = MinerUStub({("POST", "/tasks"): [(200, {"task_id": "t"})], ("GET", "/tasks/t"): [(200, task)]})
    with pytest.raises(MinerUError) as caught:
        await client(failed).parse("a.pdf", PDF, "application/pdf")
    assert (caught.value.code, caught.value.retryable) == (code, retryable)
    assert "Device" not in caught.value.message, "原始错误只进日志"


async def test_lost_task_is_retryable():
    lost = MinerUStub({("POST", "/tasks"): [(200, {"task_id": "t"})], ("GET", "/tasks/t"): [(404, b"")]})
    with pytest.raises(MinerUError) as caught:
        await client(lost).parse("a.pdf", PDF, "application/pdf")
    assert caught.value.retryable


async def test_poll_deadline_is_a_timeout():
    stub = MinerUStub({("POST", "/tasks"): [(200, {"task_id": "t"})], ("GET", "/tasks/t"): [(200, {"status": "running"})]})
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
    stub = MinerUStub({("POST", "/tasks"): [error]})
    with pytest.raises(MinerUError) as caught:
        await client(stub).parse("a.pdf", PDF, "application/pdf")
    assert caught.value.retryable and "10.0.0.1" not in caught.value.message


@pytest.mark.parametrize("payload", [{"task_id": ""}, b"not json"])
async def test_malformed_submit(payload):
    stub = MinerUStub({("POST", "/tasks"): [(200, payload)]})
    with pytest.raises(MinerUError) as caught:
        await client(stub).parse("a.pdf", PDF, "application/pdf")
    assert caught.value.code == "parser_protocol"


@pytest.mark.parametrize("result", [{}, {"results": {}}, {"results": {"a": {"md": 1}}}, {"results": {"a": "x"}}])
async def test_malformed_result(result):
    stub = MinerUStub({("POST", "/tasks"): [(404, b"")], ("POST", "/file_parse"): [(200, result)]})
    with pytest.raises(MinerUError) as caught:
        await client(stub).parse("a.pdf", PDF, "application/pdf")
    assert caught.value.code == "parser_protocol"


async def test_result_and_sync_errors_are_mapped():
    stub = MinerUStub({("POST", "/tasks"): [(200, {"task_id": "t"})], ("GET", "/tasks/t"): [(200, {"status": "done"})],
                       ("GET", "/tasks/t/result"): [(502, b"")]})
    with pytest.raises(MinerUError) as caught:
        await client(stub).parse("a.pdf", PDF, "application/pdf")
    assert caught.value.code == "service_unavailable"
    sync = MinerUStub({("POST", "/tasks"): [(405, b"")], ("POST", "/file_parse"): [(415, b"")]})
    with pytest.raises(MinerUError) as caught:
        await client(sync).parse("a.pdf", PDF, "application/pdf")
    assert caught.value.code == "unsupported_format"
    status = MinerUStub({("POST", "/tasks"): [(200, {"task_id": "t"})], ("GET", "/tasks/t"): [(503, b"")]})
    with pytest.raises(MinerUError):
        await client(status).parse("a.pdf", PDF, "application/pdf")


async def test_full_result_brings_layout_and_images():
    """知识库要 middle_json 与图片：middle_json 有时是字符串、图片是 data URI，名字里带的路径去掉。"""
    middle = {"pdf_info": [{"page_idx": 0, "para_blocks": []}]}
    png = b"\x89PNG\r\n"
    result = {"results": {"a.pdf": {"md_content": "# 标题", "middle_json": json.dumps(middle),
                                    "images": {"images/x.png": "data:image/png;base64," + base64.b64encode(png).decode()}}}}
    stub = MinerUStub({("POST", "/tasks"): [(200, {"task_id": "t"})], ("GET", "/tasks/t"): [(200, {"status": "done"})],
                       ("GET", "/tasks/t/result"): [(200, result)]})
    full = await client(stub, backend="hybrid-http-client", effort="high", server_url="http://127.0.0.1:30000").parse_full(
        "a.pdf", PDF, "application/pdf", image_analysis=True)
    assert (full.markdown, full.middle, full.images) == ("# 标题", middle, {"x.png": png})
    form = stub.requests[0].content.decode()
    for field, value in (("return_middle_json", "true"), ("return_images", "true"), ("effort", "high"),
                         ("image_analysis", "true"), ("server_url", "http://127.0.0.1:30000")):
        assert f'name="{field}"\r\n\r\n{value}' in form, field


def test_backends_that_need_a_vlm_server_fall_back_to_pipeline_without_one():
    """hybrid、vlm 的 http-client 后端要 VLM 推理服务（30000 端口那个）；没配地址就退回 pipeline，不然 MinerU 直接报错。"""
    without = usable(MinerUOptions(backend="hybrid-http-client", effort="high"))
    assert (without.backend, without.effort) == ("pipeline", None)
    with_vlm = usable(MinerUOptions(backend="hybrid-http-client", effort="high", server_url="http://vlm:30000"))
    assert (with_vlm.backend, with_vlm.server_url) == ("hybrid-http-client", "http://vlm:30000")
    assert usable(MinerUOptions(backend="pipeline")).backend == "pipeline"


async def test_pipeline_does_not_get_hybrid_only_fields():
    stub = MinerUStub({("POST", "/tasks"): [(200, {"task_id": "t"})], ("GET", "/tasks/t"): [(200, {"status": "done"})],
                       ("GET", "/tasks/t/result"): [(200, {"results": {"a": {"md_content": "x"}}})]})
    await client(stub, effort="high", server_url="http://vlm:30000").parse_full("a.pdf", PDF, "application/pdf",
                                                                               image_analysis=True)
    form = stub.requests[0].content.decode()
    assert 'name="effort"' not in form and 'name="image_analysis"' not in form and 'name="server_url"' not in form


async def test_vlm_backend_gets_its_server_but_not_hybrid_only_fields():
    """强制 VLM 模式（vlm-http-client）：带上 VLM 服务地址；effort、image_analysis 只对 hybrid 有用，不发。"""
    stub = MinerUStub({("POST", "/tasks"): [(200, {"task_id": "t"})], ("GET", "/tasks/t"): [(200, {"status": "done"})],
                       ("GET", "/tasks/t/result"): [(200, {"results": {"a": {"md_content": "x"}}})]})
    await client(stub, backend="vlm-http-client", effort="high", server_url="http://vlm:30000").parse_full(
        "a.pdf", PDF, "application/pdf", image_analysis=True)
    form = stub.requests[0].content.decode()
    assert 'name="backend"\r\n\r\nvlm-http-client' in form and 'name="server_url"\r\n\r\nhttp://vlm:30000' in form
    assert 'name="effort"' not in form and 'name="image_analysis"' not in form


async def test_markdown_only_parse_does_not_ask_for_layout():
    stub = MinerUStub({("POST", "/tasks"): [(200, {"task_id": "t"})], ("GET", "/tasks/t"): [(200, {"status": "done"})],
                       ("GET", "/tasks/t/result"): [(200, {"results": {"a": {"md_content": "x"}}})]})
    assert await client(stub).parse("a.pdf", PDF, "application/pdf") == "x"
    form = stub.requests[0].content.decode()
    assert 'name="return_images"\r\n\r\nfalse' in form and 'name="image_analysis"' not in form


@pytest.mark.parametrize("bad", [{"middle_json": "not json"}, {"middle_json": [1]}, {"images": {"a": "%%%"}},
                                 {"images": ["a"]}])
async def test_malformed_full_results_are_protocol_errors(bad):
    stub = MinerUStub({("POST", "/tasks"): [(404, b"")],
                       ("POST", "/file_parse"): [(200, {"results": {"a": {"md_content": "x", **bad}}})]})
    with pytest.raises(MinerUError) as caught:
        await client(stub).parse_full("a.pdf", PDF, "application/pdf")
    assert caught.value.code == "parser_protocol"
