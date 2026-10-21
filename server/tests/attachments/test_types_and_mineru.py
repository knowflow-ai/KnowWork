"""附件类型判定（扩展名 + 魔数）与 MinerU 3.x 客户端（MockTransport 当桩，不连真实服务）。"""

import json

import httpx
import pytest

from knowwork_server.attachments.mineru import MinerUClient, MinerUError, MinerUOptions, build_mineru_client
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


async def test_task_failure_and_lost_task():
    failed = MinerUStub({("POST", "/tasks"): [(200, {"task_id": "t"})], ("GET", "/tasks/t"): [(200, {"status": "failed"})]})
    with pytest.raises(MinerUError) as caught:
        await client(failed).parse("a.pdf", PDF, "application/pdf")
    assert caught.value.code == "file_corrupt"
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


async def test_build_client_sets_base_url():
    stub = MinerUStub({("POST", "/tasks"): [(404, b"")], ("POST", "/file_parse"): [(200, RESULT)]})
    mineru, http = build_mineru_client("http://mineru:8000", MinerUOptions(), transport=httpx.MockTransport(stub.handler))
    assert await mineru.parse("a.pdf", PDF, "application/pdf") == "# 解析结果"
    assert str(stub.requests[0].url).startswith("http://mineru:8000/tasks")
    await http.aclose()
