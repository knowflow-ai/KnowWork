"""模型网关这一侧的敏感词检查，只管个人专属宿主的调用（模型令牌带归属，见 kernel/tokens.py）。

为什么要有：专属宿主里跑着用户自己上传、没审过的插件，和宿主侧的检查插件（content-guard.mjs）在同一个进程里，插件可以
把它停掉、绕过去，甚至拿着模型令牌直接调网关。要强制的管控得放在宿主够不着的地方。共享宿主里只有我们审过的插件，
不经这里，热路径不变。

查什么，方向同宿主插件（「检查输入」管送给模型的，「检查输出」管模型写的）：
- 输入：请求里系统、用户、工具结果这些消息的文字，和工具定义（名字、说明、参数说明）。宿主插件只查员工的消息和工具结果，
  这里多查系统提示和工具定义（插件能把文字藏进去），所以专属宿主里系统提示（如专家的提示词）带敏感词也会被拦，比共享宿主严；
- 输出：请求里模型以前写的消息（插件能伪造历史）和这次流式吐出的正文、推理、工具参数。

做法照模型网关常见的护栏（调用前查整份请求，流式输出边转边查）：
- 输入命中：不调模型，回 400，错误文字以 BLOCKED 开头、后面是管理员写的阻断提示；
- 输出：按 SSE 事件解开，正文和推理每攒够 CHECK_EVERY_CHARS 个字（或隔了 CHECK_EVERY_SECONDS）查一次新增的部分，往回带
  holdback 个字（跨两次的词也查得到），查过才转给宿主；工具调用一出现，后面的事件整段扣住，到结束时把完整参数解开查，
  通过才放（参数里的中文可能是转义过的，拼完解开才查得准）；
- 命中以后内容一律不转，读完上游、把用量转出去（用量照记），最后发一条错误事件，文字同样以 BLOCKED 开头。官方适配器把它
  当成这次调用失败（非重试类），宿主插件认出这个开头，换成和它自己拦下时一样的结束（CONTENT_BLOCKED 加阻断提示），
  员工看到的是同一句话；插件被绕开时员工看到的是调用失败；
- 检查接口不通：不放行（不可用不等于通过），错误文字以 UNAVAILABLE 开头。
"""

import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .content_check import CheckRefused, CheckUnavailable, ContentCheckClient, GatePolicy, Verdict

# 宿主插件认这两个开头（content-guard-stream.mjs 的 gatewayEndings），改了两边一起改
BLOCKED = "knowwork-content-blocked"
UNAVAILABLE = "knowwork-check-unavailable"
UNAVAILABLE_MESSAGE = "内容检查暂时不可用，请稍后再试"
CHECK_EVERY_CHARS = 64
CHECK_EVERY_SECONDS = 0.2
MAX_JSON_DEPTH = 32

Scan = Callable[[str, list[str], str | None], Awaitable[Verdict]]
Wrap = Callable[[AsyncIterator[bytes]], AsyncIterator[bytes]]


@dataclass(frozen=True)
class Refusal:
    """不调模型，直接回给宿主的错误。"""

    status: int
    message: str


def ending_text(verdict: Verdict | None) -> str:
    """verdict 为 None 表示查不成。"""
    if verdict is None:
        return f"{UNAVAILABLE}: {UNAVAILABLE_MESSAGE}"
    return f"{BLOCKED}: {verdict.message}"


# ---- 请求里的文字 ----

def _strings(value: Any, depth: int = 0) -> list[str]:
    """JSON 里全部的键和字符串值（转义过的中文解开以后才查得到）。"""
    if depth > MAX_JSON_DEPTH:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [text for item in value for text in _strings(item, depth + 1)]
    if isinstance(value, dict):
        return [text for key, item in value.items() for text in (str(key), *_strings(item, depth + 1))]
    return []


def argument_texts(raw: Any) -> list[str]:
    """工具参数：解开 JSON 取键和字符串值；解不开就查原文（同宿主插件的 argumentTexts）。"""
    if not isinstance(raw, str) or not raw:
        return []
    try:
        return _strings(json.loads(raw))
    except ValueError:
        return [raw]


def _message_texts(message: dict[str, Any]) -> list[str]:
    found: list[str] = []
    content = message.get("content")
    if isinstance(content, str):
        found.append(content)
    elif isinstance(content, list):  # 多段内容：只取文字段，图片是 data: 地址，不查
        found.extend(part["text"] for part in content if isinstance(part, dict) and isinstance(part.get("text"), str))
    found.extend(message[key] for key in ("reasoning_content", "reasoning", "refusal", "name")
                 if isinstance(message.get(key), str))
    for call in message.get("tool_calls") or []:
        function = call.get("function") if isinstance(call, dict) else None
        if isinstance(function, dict):
            found.extend(text for text in (function.get("name"),) if isinstance(text, str))
            found.extend(argument_texts(function.get("arguments")))
    return [text for text in found if text]


def request_segments(body: dict[str, Any]) -> tuple[list[str], list[str]]:
    """(送给模型看的, 模型以前写的)：一条消息、一个工具定义各一段。"""
    given: list[str] = []
    written: list[str] = []
    for message in body.get("messages") or []:
        if not isinstance(message, dict):
            continue
        texts = _message_texts(message)
        if texts:
            (written if message.get("role") == "assistant" else given).append("\n".join(texts))
    for tool in body.get("tools") or []:
        texts = [text for text in _strings(tool) if text]
        if texts:
            given.append("\n".join(texts))
    return given, written


# ---- 流式输出 ----

def _event(payload: dict[str, Any]) -> bytes:
    return b"data: " + json.dumps(payload, ensure_ascii=False).encode() + b"\n\n"


def _usage_only(payload: dict[str, Any]) -> bytes | None:
    """命中以后只把用量转出去：官方适配器先读 usage、没有 choice 就跳过，这一步的用量照样记上。"""
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return None
    return _event({key: payload[key] for key in ("id", "object", "created", "model") if key in payload}
                  | {"choices": [], "usage": usage})


@dataclass
class _Text:
    tail: str = ""  # 查过的最后 holdback 个字
    pending: str = ""


@dataclass
class _Tool:
    name: str = ""
    arguments: str = ""


@dataclass
class _Stream:
    texts: dict[tuple[int, str], _Text] = field(default_factory=dict)
    tools: dict[tuple[int, int], _Tool] = field(default_factory=dict)
    held: list[tuple[bytes, Any]] = field(default_factory=list)  # 还没转出去的事件和它解开的内容

    def take(self, payload: Any) -> bool:
        """记下这个事件里的增量；返回它有没有结束原因。"""
        finished = False
        choices = payload.get("choices") if isinstance(payload, dict) else None
        for choice in choices if isinstance(choices, list) else []:
            if not isinstance(choice, dict):
                continue
            index = choice.get("index") if isinstance(choice.get("index"), int) else 0
            delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
            for kind in ("content", "reasoning_content", "reasoning"):
                if isinstance(delta.get(kind), str) and delta[kind]:
                    text = self.texts.setdefault((index, kind), _Text())
                    text.pending += delta[kind]
            for call in delta.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                slot = call.get("index") if isinstance(call.get("index"), int) else 0
                tool = self.tools.setdefault((index, slot), _Tool())
                function = call.get("function") if isinstance(call.get("function"), dict) else {}
                tool.name += function.get("name") if isinstance(function.get("name"), str) else ""
                tool.arguments += function.get("arguments") if isinstance(function.get("arguments"), str) else ""
            finished = finished or bool(choice.get("finish_reason"))
        return finished

    def pending_chars(self) -> int:
        return sum(len(text.pending) for text in self.texts.values())

    def windows(self, holdback: int) -> list[str]:
        """各段新增的部分接上前面留着的尾巴；取完把尾巴往后挪。"""
        found: list[str] = []
        for text in self.texts.values():
            if text.pending:
                window = text.tail + text.pending
                found.append(window)
                text.tail = window[-holdback:] if holdback > 0 else ""
                text.pending = ""
        return found


def _split(buffer: bytes) -> tuple[list[bytes], bytes]:
    *events, rest = buffer.replace(b"\r\n", b"\n").split(b"\n\n")
    return [event for event in events if event.strip()], rest


def _payload(event: bytes) -> Any:
    lines = [line[5:].strip() for line in event.split(b"\n") if line.startswith(b"data:")]
    if not lines:
        return None
    data = b"\n".join(lines)
    if data == b"[DONE]":
        return "[DONE]"
    try:
        return json.loads(data)
    except ValueError:
        return None


async def guard_stream(source: AsyncIterator[bytes], scan: Scan, holdback: int, *,
                       clock: Callable[[], float] = time.monotonic) -> AsyncIterator[bytes]:
    """包住上游的 SSE 字节流，边转边查（规则见文件开头）。"""
    stream = _Stream()
    ending: str | None = None
    checked_at = clock()
    buffer = b""

    async def verify(final: bool) -> Verdict | None:
        """查新增的正文与推理；final 时再查工具参数。干净返回 Verdict(False)，查不成返回 None。"""
        try:
            windows = stream.windows(holdback)
            verdict = await scan("output", windows, None) if windows else Verdict(blocked=False)
            for tool in stream.tools.values() if final and not verdict.blocked else ():
                verdict = await scan("tool_args", argument_texts(tool.arguments) or [tool.name], tool.name or None)
                if verdict.blocked:
                    break
            return verdict
        except (CheckUnavailable, CheckRefused):
            return None

    async for chunk in source:
        events, buffer = _split(buffer + chunk)
        for raw in events:
            payload = _payload(raw)
            if ending is not None:
                usage = _usage_only(payload)
                if usage is not None:
                    yield usage
                continue
            stream.held.append((raw + b"\n\n", payload))
            final = stream.take(payload) or payload == "[DONE]"
            if not (final or stream.pending_chars() >= CHECK_EVERY_CHARS
                    or clock() - checked_at >= CHECK_EVERY_SECONDS):
                continue
            verdict = await verify(final)
            checked_at = clock()
            if verdict is None or verdict.blocked:
                ending = ending_text(verdict)
                for _, held in stream.held:
                    usage = _usage_only(held)
                    if usage is not None:
                        yield usage
                stream.held = []
                continue
            if final:
                stream.tools = {}  # 参数查过了；后面只剩用量和 [DONE]，不用再扣
            if final or not stream.tools:
                for held, _ in stream.held:
                    yield held
                stream.held = []
    if ending is None and stream.held:
        # 上游没给结束原因就断了：剩下的照样查完再放
        verdict = await verify(True)
        if verdict is None or verdict.blocked:
            ending = ending_text(verdict)
            for _, held in stream.held:
                usage = _usage_only(held)
                if usage is not None:
                    yield usage
        else:
            for held, _ in stream.held:
                yield held
    if ending is not None:
        yield _event({"error": {"message": ending, "type": "content_blocked"}})


# ---- 网关接上 ----

class ContentGuard:
    def __init__(self, client: ContentCheckClient) -> None:
        self._client = client

    async def admit(self, workspace_id: str, authorization: str, body: dict[str, Any]) -> Refusal | Wrap | None:
        """调模型之前：查请求，命中或查不成回 Refusal；要查输出时回包流的函数；都不用查回 None。"""
        client = self._client
        try:
            policy = await client.policy(workspace_id, authorization)
            if not policy.input and not policy.output:
                return None
            given, written = request_segments(body)
            for direction, segments, on in (("input", given, policy.input), ("output", written, policy.output)):
                verdict = await client.check(workspace_id, authorization, policy, direction=direction,
                                             segments=segments, remember=True) if on else Verdict(blocked=False)
                if verdict.blocked:
                    return Refusal(400, ending_text(verdict))
        except CheckRefused:
            return Refusal(403, "已经不是这个空间的成员")
        except CheckUnavailable:
            return Refusal(503, ending_text(None))
        if not policy.output:
            return None
        if body.get("stream") is not True:
            # 官方适配器总是流式调用；不流式的整段回答没法边转边查，干脆不接
            return Refusal(400, "开了输出检查时只支持流式调用")
        return self._wrap(workspace_id, authorization, policy)

    def _wrap(self, workspace_id: str, authorization: str, policy: GatePolicy) -> Wrap:
        client = self._client

        async def scan(direction: str, segments: list[str], tool: str | None) -> Verdict:
            return await client.check(workspace_id, authorization, policy, direction=direction, segments=segments,
                                      tool=tool)

        def wrap(source: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
            return guard_stream(source, scan, policy.holdback)

        return wrap
