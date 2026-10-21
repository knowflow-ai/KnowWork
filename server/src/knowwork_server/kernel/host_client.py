"""内核合同的唯一实现：通过宿主 HTTP 协议调用 DSH 运行时宿主。

两个 HTTP 客户端：
- 控制请求（建会话、发送、取消、审批、回答提问、轮换）共用一个有连接上限的客户端，整体 30 秒超时；
- 事件订阅是长连接，每个会话占一条，单独用一个不限连接数的客户端，读超时 15 秒
  （宿主每 2 秒发一行心跳，15 秒读不到任何字节说明连接已死）。
订阅长连接因此不会占满控制请求的连接池。
"""

import json
import logging
from collections.abc import AsyncIterator
from urllib.parse import quote

import httpx
from pydantic import ValidationError

from .contract import (
    INVALID_FRAME_KIND,
    CancelResult,
    EpochMismatch,
    KernelError,
    KernelEvent,
    PendingApproval,
    PendingQuestion,
    Question,
    QuestionAnswer,
    QuestionOption,
    SessionBusy,
    SessionNotFound,
    SessionSpec,
    TurnAttachment,
    TurnInput,
)
from .locale import resolve_turn_language

log = logging.getLogger(__name__)

_ERRORS = {"session_not_found": SessionNotFound, "session_busy": SessionBusy, "epoch_mismatch": EpochMismatch}

CONTROL_TIMEOUT = httpx.Timeout(30.0)
STREAM_TIMEOUT = httpx.Timeout(30.0, read=15.0)


def _raise_for(response: httpx.Response) -> None:
    if response.is_success:
        return
    try:
        body = response.json()
    except ValueError:
        body = None
    error = body.get("error") if isinstance(body, dict) else None
    error = error if isinstance(error, dict) else {}
    code = str(error.get("code", "internal"))
    raise _ERRORS.get(code, KernelError)(code, str(error.get("message", "宿主返回错误")), response.status_code)


def _segment(value: str) -> str:
    """路径参数一律整段编码，`/`、`?` 等字符不能改变请求的路由。"""
    return quote(value, safe="")


def _question(raw: dict) -> Question:
    options = tuple(QuestionOption(label=str(o["label"]), description=o.get("description"))
                    for o in raw.get("options") or ())
    return Question(id=str(raw["id"]), question=str(raw["question"]), header=raw.get("header"),
                    detail=raw.get("detail"), options=options, multi_select=raw.get("multiSelect") is True)


def _answer_body(answer: QuestionAnswer) -> dict:
    return {"answers": [{"id": item.id, "selected": list(item.selected),
                         **({} if item.custom is None else {"custom": item.custom})} for item in answer.answers]}


def parse_frame(line: str) -> KernelEvent | None:
    """把一行 NDJSON 解析成事件帧。

    - 空行是心跳，返回 None；
    - 能读出 epoch 与 cursor 但其余字段不合法时，返回 kernel.invalid 占位帧，让调用方推进游标；
    - 连游标都读不出来的行无法定位，记日志后丢弃（返回 None），不阻塞后续帧。
    """
    if not line.strip():
        return None
    try:
        raw = json.loads(line)
    except ValueError:
        log.warning("host frame is not valid json, dropped (%d bytes)", len(line))
        return None
    try:
        return KernelEvent.model_validate(raw)
    except ValidationError:
        pass
    epoch = raw.get("epoch") if isinstance(raw, dict) else None
    cursor = raw.get("cursor") if isinstance(raw, dict) else None
    usable = isinstance(epoch, str) and 0 < len(epoch) <= 64 and type(cursor) is int and cursor >= 0
    if not usable:
        log.warning("host frame without usable epoch/cursor, dropped")
        return None
    kind = raw.get("kind")
    log.warning("host frame %s/%s failed validation, stored as placeholder", epoch, cursor)
    return KernelEvent(epoch=epoch, cursor=cursor, kind=INVALID_FRAME_KIND,
                       data={"reason": "invalid_frame", "kind": None if kind is None else str(kind)[:64]})


class HostKernel:
    def __init__(self, base_url: str, token: str, *, client: httpx.AsyncClient | None = None,
                 stream_client: httpx.AsyncClient | None = None) -> None:
        headers = {"authorization": f"Bearer {token}"}
        self._client = client or httpx.AsyncClient(base_url=base_url, headers=headers, timeout=CONTROL_TIMEOUT)
        self._stream_client = stream_client or httpx.AsyncClient(
            base_url=base_url, headers=headers, timeout=STREAM_TIMEOUT,
            limits=httpx.Limits(max_connections=None, max_keepalive_connections=20),
        )

    async def aclose(self) -> None:
        await self._client.aclose()
        await self._stream_client.aclose()

    async def open_session(self, spec: SessionSpec) -> str:
        policy: dict = {"deny": list(spec.policy.deny)} if spec.policy.deny else {}
        if spec.policy.gate is not None:
            gate = {} if spec.policy.gate.required_tool is None else {"requiredTool": spec.policy.gate.required_tool}
            if spec.policy.gate.max_nudges is not None:
                gate["maxNudges"] = spec.policy.gate.max_nudges
            policy["gate"] = gate
        body: dict = {"sessionId": spec.session_id, "policy": policy, "resume": spec.resume}
        if spec.tools_token is not None:
            body["tools"] = {"token": spec.tools_token}
        if spec.skills_token is not None:
            body["skills"] = {"token": spec.skills_token}
        response = await self._client.post("/v1/sessions", json=body)
        _raise_for(response)
        return str(response.json()["epoch"])

    async def send(self, session_id: str, turn: TurnInput) -> str:
        body = {"text": turn.text, "mode": turn.mode, "language": resolve_turn_language(turn.text, turn.language)}
        if turn.attachments:
            body["attachments"] = [_attachment(a) for a in turn.attachments]
        response = await self._client.post(f"/v1/sessions/{_segment(session_id)}/messages", json=body)
        _raise_for(response)
        return str(response.json()["messageId"])

    async def cancel(self, session_id: str) -> CancelResult:
        response = await self._client.post(f"/v1/sessions/{_segment(session_id)}/cancel")
        _raise_for(response)
        data = response.json()
        return CancelResult(settled=bool(data["settled"]), status=str(data["status"]))

    async def dispose(self, session_id: str) -> None:
        _raise_for(await self._client.delete(f"/v1/sessions/{_segment(session_id)}"))

    async def pending_approvals(self, session_id: str) -> list[PendingApproval]:
        response = await self._client.get(f"/v1/sessions/{_segment(session_id)}/approvals")
        _raise_for(response)
        return [PendingApproval(id=a["id"], tool_name=a["toolName"], call_id=a.get("callId"), reason=a.get("reason"))
                for a in response.json()]

    async def decide_approval(self, session_id: str, approval_id: str, allow: bool) -> None:
        outcome = "allowed-once" if allow else "rejected"
        path = f"/v1/sessions/{_segment(session_id)}/approvals/{_segment(approval_id)}"
        _raise_for(await self._client.post(path, json={"outcome": outcome}))

    async def pending_questions(self, session_id: str) -> list[PendingQuestion]:
        response = await self._client.get(f"/v1/sessions/{_segment(session_id)}/questions")
        _raise_for(response)
        return [PendingQuestion(id=str(q["id"]), state=q["state"], questions=tuple(_question(i) for i in q["questions"]))
                for q in response.json()]

    async def answer_question(self, session_id: str, question_id: str, answer: QuestionAnswer) -> None:
        path = f"/v1/sessions/{_segment(session_id)}/questions/{_segment(question_id)}"
        _raise_for(await self._client.post(path, json=_answer_body(answer)))

    async def rotate_model_credential(self, token: str) -> None:
        _raise_for(await self._client.put("/v1/model-credential", json={"token": token}))

    async def rotate_tool_credential(self, session_id: str, token: str) -> None:
        _raise_for(await self._client.put(f"/v1/sessions/{_segment(session_id)}/tool-credential", json={"token": token}))

    async def rotate_skill_credential(self, session_id: str, token: str) -> None:
        path = f"/v1/sessions/{_segment(session_id)}/skill-credential"
        _raise_for(await self._client.put(path, json={"token": token}))

    async def subscribe(self, session_id: str, *, after: int = 0, epoch: str | None = None) -> AsyncIterator[KernelEvent]:
        params: dict[str, str | int] = {"after": after}
        if epoch is not None:
            params["epoch"] = epoch
        path = f"/v1/sessions/{_segment(session_id)}/events"
        async with self._stream_client.stream("GET", path, params=params) as response:
            if not response.is_success:
                await response.aread()
                _raise_for(response)
            async for line in response.aiter_lines():
                frame = parse_frame(line)
                if frame is not None:
                    yield frame


def _attachment(item: TurnAttachment) -> dict:
    body = {"name": item.name, "path": item.path, "bytes": item.bytes, "sha256": item.sha256}
    return body if item.extracted_path is None else {**body, "extractedPath": item.extracted_path}
