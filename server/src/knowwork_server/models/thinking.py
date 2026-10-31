"""对话模型的「思考」开关：管理员在「管理 → 模型」里给对话模型定开、关或跟随服务默认，转发时按服务商的写法补参数。

不同服务商关思考的写法不一样（按接口地址认服务商，认不出的当成自建服务）：
- 自建的 vLLM、SGLang、Xinference 等：chat_template_kwargs.enable_thinking（两家都写明是按请求覆盖的开关）；
- 阿里百炼、硅基流动、魔搭、腾讯混元：顶层 enable_thinking；
- DeepSeek、智谱、火山方舟、Moonshot、MiniMax：thinking.type 为 enabled 或 disabled；
- OpenAI：推理模型关不掉推理，不补（补了反而可能被拒）。
Qwen3 这类混合模型在自建服务上默认开着思考：每一步先想一段再动手，回答要多等几秒（2026-10-23 实测一轮 15 秒里思考占 7 秒）。
跟随服务默认时请求原样不动。宿主自己带了思考参数时以管理员的设置为准。
"""

from typing import Any, Literal
from urllib.parse import urlsplit

Thinking = Literal["on", "off"]
THINKING_FIELDS = ("enable_thinking", "thinking", "reasoning_effort")

_ENABLE_THINKING = ("dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com", "siliconflow.cn", "siliconflow.com",
                    "modelscope.cn", "hunyuan.cloud.tencent.com")
_THINKING_TYPE = ("deepseek.com", "bigmodel.cn", "volces.com", "moonshot.cn", "moonshot.ai", "minimaxi.com",
                  "minimax.chat", "minimax.io")
_NO_SWITCH = ("openai.com",)


def _matches(host: str, domains: tuple[str, ...]) -> bool:
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)


def _format(base_url: str) -> str:
    host = (urlsplit(base_url).hostname or "").lower()
    if _matches(host, _NO_SWITCH):
        return "none"
    if _matches(host, _ENABLE_THINKING):
        return "enable_thinking"
    if _matches(host, _THINKING_TYPE):
        return "thinking_type"
    return "chat_template_kwargs"


def with_thinking(body: dict[str, Any], thinking: Thinking | None, base_url: str) -> dict[str, Any]:
    """按模型的思考设置补参数，返回新的请求体（不改传进来的）。thinking 为空是跟随服务默认：原样返回。"""
    if thinking is None:
        return body
    style = _format(base_url)
    if style == "none":
        return body
    enabled = thinking == "on"
    cleaned = {key: value for key, value in body.items() if key not in THINKING_FIELDS}
    template = {key: value for key, value in (body.get("chat_template_kwargs") or {}).items()
                if key != "enable_thinking"}
    if style == "chat_template_kwargs":
        return {**cleaned, "chat_template_kwargs": {**template, "enable_thinking": enabled}}
    if template:
        cleaned["chat_template_kwargs"] = template
    else:
        cleaned.pop("chat_template_kwargs", None)
    if style == "enable_thinking":
        return {**cleaned, "enable_thinking": enabled}
    return {**cleaned, "thinking": {"type": "enabled" if enabled else "disabled"}}
