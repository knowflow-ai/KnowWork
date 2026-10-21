"""宿主客户端：坏帧不阻塞、订阅与控制请求分池、超时、路径参数编码、错误体解析。"""

import json

import httpx
import pytest

from knowwork_server.kernel.contract import (
    INVALID_FRAME_KIND,
    KernelError,
    QuestionAnswer,
    QuestionAnswerItem,
    SessionNotFound,
    SessionPolicy,
    SessionSpec,
    SkillGate,
    TurnAttachment,
    TurnInput,
)
from knowwork_server.kernel.host_client import CONTROL_TIMEOUT, STREAM_TIMEOUT, HostKernel, parse_frame
from knowwork_server.kernel.locale import resolve_turn_language

pytestmark = pytest.mark.anyio


def test_parse_valid_frame_ignores_unknown_fields():
    frame = parse_frame(json.dumps({"epoch": "e", "cursor": 3, "kind": "agent/status", "time": 1,
                                    "data": {"status": "idle"}, "newField": 1}))
    assert frame is not None and frame.cursor == 3 and frame.schema_version == "kernel.v1"


@pytest.mark.parametrize("line", ["", "   ", "{not json", json.dumps({"kind": "x"}), json.dumps([1, 2]),
                                  json.dumps({"epoch": "e", "cursor": -1, "kind": "x"})])
def test_unlocatable_lines_are_dropped(line):
    assert parse_frame(line) is None


@pytest.mark.parametrize("raw", [
    {"epoch": "e", "cursor": 4, "kind": "", "time": 1},
    {"epoch": "e", "cursor": 4, "kind": "k" * 300, "time": 1},
    {"epoch": "e", "cursor": 4, "time": "yesterday", "kind": "x"},
])
def test_invalid_but_locatable_frames_become_placeholders(raw):
    frame = parse_frame(json.dumps(raw))
    assert frame is not None and frame.kind == INVALID_FRAME_KIND and frame.cursor == 4
    assert frame.data["reason"] == "invalid_frame"


async def test_bad_frames_do_not_stop_the_subscription():
    lines = [
        json.dumps({"epoch": "e", "cursor": 1, "kind": "a", "time": 1}),
        "{garbage",
        json.dumps({"epoch": "e", "cursor": 2, "kind": "", "time": 1}),
        "",
        json.dumps({"epoch": "e", "cursor": 3, "kind": "b", "time": 1}),
    ]

    def handler(request):
        return httpx.Response(200, content="\n".join(lines).encode())

    stream = httpx.AsyncClient(base_url="http://h", transport=httpx.MockTransport(handler))
    kernel = HostKernel("http://h", "t" * 32, stream_client=stream)
    frames = [f async for f in kernel.subscribe("s1")]
    assert [(f.cursor, f.kind) for f in frames] == [(1, "a"), (2, INVALID_FRAME_KIND), (3, "b")]
    await kernel.aclose()


async def test_control_and_stream_clients_are_separate_with_timeouts():
    kernel = HostKernel("http://h", "t" * 32)
    assert kernel._client is not kernel._stream_client
    assert kernel._client.timeout == CONTROL_TIMEOUT and CONTROL_TIMEOUT.read == 30.0
    assert kernel._stream_client.timeout == STREAM_TIMEOUT and STREAM_TIMEOUT.read == 15.0
    pool = kernel._stream_client._transport._pool
    assert pool._max_connections >= 2**31, "订阅客户端不应有连接数上限"
    await kernel.aclose()


async def test_path_segments_are_encoded():
    seen = []

    def handler(request):
        seen.append(request.url.raw_path.decode())
        return httpx.Response(200, json={"approvalId": "x", "outcome": "rejected"})

    client = httpx.AsyncClient(base_url="http://h", transport=httpx.MockTransport(handler))
    kernel = HostKernel("http://h", "t" * 32, client=client)
    await kernel.decide_approval("s1", "../../model-credential?x=1", allow=False)
    assert seen == ["/v1/sessions/s1/approvals/..%2F..%2Fmodel-credential%3Fx%3D1"]
    await kernel.aclose()


@pytest.mark.parametrize(("status", "body", "error_type", "code"), [
    (404, {"error": {"code": "session_not_found", "message": "x"}}, SessionNotFound, "session_not_found"),
    (500, "plain text", KernelError, "internal"),
    (502, ["not", "an", "object"], KernelError, "internal"),
    (503, {"error": "string"}, KernelError, "internal"),
])
async def test_error_bodies_of_any_shape_are_mapped(status, body, error_type, code):
    def handler(request):
        if isinstance(body, str):
            return httpx.Response(status, content=body.encode())
        return httpx.Response(status, json=body)

    client = httpx.AsyncClient(base_url="http://h", transport=httpx.MockTransport(handler))
    kernel = HostKernel("http://h", "t" * 32, client=client)
    with pytest.raises(error_type) as caught:
        await kernel.send("s1", TurnInput(text="hi"))
    assert caught.value.code == code and caught.value.status == status
    await kernel.aclose()


async def test_send_carries_turn_attachments_only_when_present():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(202, json={"messageId": "m1", "mode": "followup"})

    client = httpx.AsyncClient(base_url="http://h", transport=httpx.MockTransport(handler))
    kernel = HostKernel("http://h", "t" * 32, client=client)
    file = TurnAttachment(name="合同.docx", path="/workspace/input/合同.docx", bytes=10, sha256="a" * 64,
                          extracted_path="/workspace/input/合同.md")
    await kernel.send("s1", TurnInput(text="改一下", attachments=(file,)))
    await kernel.send("s1", TurnInput(text="再看看"))
    assert seen[0]["attachments"] == [{"name": "合同.docx", "path": "/workspace/input/合同.docx", "bytes": 10,
                                       "sha256": "a" * 64, "extractedPath": "/workspace/input/合同.md"}]
    assert "attachments" not in seen[1]
    await kernel.aclose()


async def test_send_carries_the_chosen_language_or_the_one_resolved_from_the_prompt():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(202, json={"messageId": "m1", "mode": "followup"})

    client = httpx.AsyncClient(base_url="http://h", transport=httpx.MockTransport(handler))
    kernel = HostKernel("http://h", "t" * 32, client=client)
    await kernel.send("s1", TurnInput(text="帮我总结 RAGFlow 的 API"))
    await kernel.send("s1", TurnInput(text="Summarize the API"))
    await kernel.send("s1", TurnInput(text="Zleap-AI/SAG-Wiki", language="zh"))  # 聊天页选了中文
    await kernel.send("s1", TurnInput(text="总结一下", language="en"))
    assert [body["language"] for body in seen] == ["zh", "en", "zh", "en"]
    await kernel.aclose()


def test_turn_language_rule_any_han_character_means_chinese():
    assert resolve_turn_language("短剧有哪些代表作品？") == "zh"
    assert resolve_turn_language("Explain 微短剧 please") == "zh"
    assert resolve_turn_language("What are micro-dramas?") == "en"
    assert resolve_turn_language("Zleap-AI/SAG-Wiki", "zh") == "zh", "显式指定的语言优先"


async def test_skills_token_and_gate_without_required_tool_reach_the_host():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.raw_path.decode(), json.loads(request.content or b"{}")))
        return httpx.Response(201, json={"sessionId": "s1", "status": "idle", "epoch": "e1"})

    client = httpx.AsyncClient(base_url="http://h", transport=httpx.MockTransport(handler))
    kernel = HostKernel("http://h", "t" * 32, client=client)
    spec = SessionSpec(session_id="s1", skills_token="s" * 40, policy=SessionPolicy(gate=SkillGate(max_nudges=1)))
    await kernel.open_session(spec)
    await kernel.rotate_skill_credential("s1", "n" * 40)
    (_, path, body), (method, rotate_path, rotate_body) = seen
    assert path == "/v1/sessions" and body["skills"] == {"token": "s" * 40}
    assert body["policy"] == {"gate": {"maxNudges": 1}} and "tools" not in body
    assert (method, rotate_path, rotate_body) == ("PUT", "/v1/sessions/s1/skill-credential", {"token": "n" * 40})
    await kernel.aclose()


async def test_questions_are_listed_and_answered_with_encoded_ids():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.raw_path.decode(), request.content.decode() or None))
        if request.method == "GET":
            return httpx.Response(200, json=[{"id": "call_1", "sessionId": "s1", "state": "continued", "timed": True,
                                              "questions": [{"id": "fmt", "question": "格式？", "multiSelect": True,
                                                             "options": [{"label": "PDF", "description": "版式"}]}]}])
        return httpx.Response(200, json={"questionId": "call_1", "state": "queued"})

    client = httpx.AsyncClient(base_url="http://h", transport=httpx.MockTransport(handler))
    kernel = HostKernel("http://h", "t" * 32, client=client)
    [pending] = await kernel.pending_questions("s1")
    assert (pending.id, pending.state) == ("call_1", "continued")
    [question] = pending.questions
    assert (question.id, question.multi_select, question.header) == ("fmt", True, None)
    assert [(o.label, o.description) for o in question.options] == [("PDF", "版式")]
    answer = QuestionAnswer(answers=(QuestionAnswerItem(id="fmt", selected=("PDF",)),
                                     QuestionAnswerItem(id="note", custom="按月")))
    await kernel.answer_question("s1", "../x?y", answer)
    assert seen[1][:2] == ("POST", "/v1/sessions/s1/questions/..%2Fx%3Fy")
    assert json.loads(seen[1][2]) == {"answers": [{"id": "fmt", "selected": ["PDF"]},
                                                  {"id": "note", "selected": [], "custom": "按月"}]}
    await kernel.aclose()


async def test_answer_errors_keep_the_host_code():
    def handler(request):
        return httpx.Response(404, json={"error": {"code": "question_not_found", "message": "x"}})

    client = httpx.AsyncClient(base_url="http://h", transport=httpx.MockTransport(handler))
    kernel = HostKernel("http://h", "t" * 32, client=client)
    with pytest.raises(KernelError) as caught:
        await kernel.answer_question("s1", "call_1", QuestionAnswer(answers=(QuestionAnswerItem(id="q"),)))
    assert (caught.value.code, caught.value.status) == ("question_not_found", 404)
    assert not isinstance(caught.value, SessionNotFound)
    await kernel.aclose()
