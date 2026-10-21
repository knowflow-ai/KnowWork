"""模型网关的请求体校验：只放行 OpenAI 兼容对话接口里我们认可的字段。

宿主是受信调用方，但请求体由模型适配器拼装，字段集随适配器升级而变；白名单让
意外字段（例如会把数据留存在厂商侧的 store、带会话标识的 prompt_cache_key）不会被原样转给厂商。
model 一律由网关改写：按别名查模型表得到的厂商模型名，或回退时配置的固定上游模型。
"""

import json
from typing import Any

ALLOWED_FIELDS = frozenset({
    "messages", "tools", "tool_choice", "parallel_tool_calls", "stream", "stream_options",
    "temperature", "top_p", "max_tokens", "max_completion_tokens", "stop", "seed",
    "presence_penalty", "frequency_penalty", "response_format",
    "reasoning_effort", "thinking", "enable_thinking", "chat_template_kwargs",
})
MAX_BODY_BYTES = 8 * 1024 * 1024


class InvalidRequest(ValueError):
    """请求体不合法。消息是给宿主看的固定文案，不含请求内容。"""


def filter_body(raw: bytes, upstream_model: str) -> dict[str, Any]:
    filtered, _ = parse_request(raw)
    return {**filtered, "model": upstream_model}


def parse_request(raw: bytes) -> tuple[dict[str, Any], str | None]:
    """校验并按白名单过滤；另外返回宿主请求的模型名（别名），过滤结果里不含 model。"""
    if len(raw) > MAX_BODY_BYTES:
        raise InvalidRequest("请求体过大")
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as error:
        raise InvalidRequest("请求体不是合法 JSON") from error
    if not isinstance(body, dict):
        raise InvalidRequest("请求体必须是 JSON 对象")
    if not isinstance(body.get("messages"), list) or not body["messages"]:
        raise InvalidRequest("messages 必须是非空数组")
    if "stream" in body and not isinstance(body["stream"], bool):
        raise InvalidRequest("stream 必须是布尔值")
    if "tools" in body and not isinstance(body["tools"], list):
        raise InvalidRequest("tools 必须是数组")
    filtered = {key: value for key, value in body.items() if key in ALLOWED_FIELDS}
    requested = body.get("model")
    return filtered, requested if isinstance(requested, str) and requested else None
