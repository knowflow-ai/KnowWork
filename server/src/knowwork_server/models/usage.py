"""用量来自宿主事件流（网关看不到会话，见 e1-report）。这里全是纯函数与不可变值。

DSH 的 `assistant/message` 事件：data = {turn, step, message, stream, usage?, interrupted?}，
usage 为 dsh-llm 的 TokenUsage：{inputTokens, outputTokens, totalTokens?, cacheReadTokens?,
cacheWriteTokens?, reasoningTokens?}。事件里没有模型名，模型别名取会话的 `request/header`
（data.config.model）或会话自己记录的模型，由调用方传入。

只取数字，不取 message 与 stream 里的文本：默认不存提示词与回答原文。
"""

import math
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

ASSISTANT_MESSAGE = "assistant/message"
REQUEST_HEADER = "request/header"
STEP_START = "step/start"


@dataclass(frozen=True)
class UsageContext:
    workspace_id: str
    user_id: str
    conversation_id: str
    model_alias: str


@dataclass(frozen=True)
class UsageEntry:
    workspace_id: str
    user_id: str
    conversation_id: str
    model_alias: str
    turn: int
    step: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cache_read_tokens: int
    reasoning_tokens: int
    duration_ms: int | None
    status: str  # ok | interrupted
    occurred_at: datetime


def _count(usage: dict, key: str) -> int | None:
    value = usage.get(key, 0)
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value) or value < 0:
        return None
    return int(value)


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def stream_duration_ms(stream: Any) -> int | None:
    """首个流式块到最后一个块的毫秒数（DSH 的流记录带时间戳；不含首块之前的等待）。"""
    if not isinstance(stream, list):
        return None
    starts: list[float] = []
    ends: list[float] = []
    for record in stream:
        if not isinstance(record, dict):
            continue
        if isinstance(record.get("time"), int | float):
            starts.append(record["time"])
            ends.append(record["time"])
        elif isinstance(record.get("time0"), int | float):
            gaps = record.get("dt") or []
            total = sum(g for g in gaps if isinstance(g, int | float)) if isinstance(gaps, list) else 0
            starts.append(record["time0"])
            ends.append(record["time0"] + total)
    if not starts:
        return None
    return max(int(max(ends) - min(starts)), 0)


def usage_from_event(kind: str, data: Any, context: UsageContext, occurred_at: datetime) -> UsageEntry | None:
    """把一条宿主事件转成用量记录；不是 assistant/message、没带用量或格式不对时返回 None。"""
    if kind != ASSISTANT_MESSAGE or not isinstance(data, dict) or not isinstance(data.get("usage"), dict):
        return None
    turn, step = _non_negative_int(data.get("turn")), _non_negative_int(data.get("step"))
    usage = data["usage"]
    counts = {key: _count(usage, key) for key in ("inputTokens", "outputTokens", "cacheReadTokens",
                                                  "reasoningTokens")}
    if turn is None or step is None or any(value is None for value in counts.values()):
        return None
    total = _count(usage, "totalTokens") if "totalTokens" in usage else None
    computed = counts["inputTokens"] + counts["outputTokens"]
    return UsageEntry(
        workspace_id=context.workspace_id, user_id=context.user_id, conversation_id=context.conversation_id,
        model_alias=context.model_alias, turn=turn, step=step,
        input_tokens=counts["inputTokens"], output_tokens=counts["outputTokens"],
        total_tokens=total if total is not None else computed,
        cache_read_tokens=counts["cacheReadTokens"], reasoning_tokens=counts["reasoningTokens"],
        duration_ms=stream_duration_ms(data.get("stream")),
        status="interrupted" if data.get("interrupted") is True else "ok", occurred_at=occurred_at)


def model_from_header(kind: str, data: Any) -> str | None:
    """从 request/header 事件取会话当前使用的模型名（即发给网关的别名）。"""
    if kind != REQUEST_HEADER or not isinstance(data, dict):
        return None
    config = data.get("config")
    model = config.get("model") if isinstance(config, dict) else None
    return model if isinstance(model, str) and model else None


@dataclass(frozen=True)
class TurnCallBudget:
    """每轮模型调用次数上限。按 step/start 计数（一步 = 一次模型调用加它请求的工具）。

    网关看不到回合，所以上限在事件流这一侧判定：exceeded 为真时由会话服务中止本轮。
    limit <= 0 表示不限。
    """

    limit: int
    turn: int | None = None
    calls: int = 0

    def after(self, kind: str, data: Any) -> "TurnCallBudget":
        if kind != STEP_START or not isinstance(data, dict):
            return self
        turn = _non_negative_int(data.get("turn"))
        if turn is None:
            return self
        if turn != self.turn:
            return replace(self, turn=turn, calls=1)
        return replace(self, calls=self.calls + 1)

    @property
    def exceeded(self) -> bool:
        return self.limit > 0 and self.calls > self.limit
