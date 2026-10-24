"""一条审计事件（不可变）与 detail 的脱敏。

detail 只允许少量标量：键名命中敏感词的整项丢弃，字符串值里像凭据的片段替换掉，超长截断。
这样调用方即使误传了提示词、密钥，也不会原样落库（落盘前脱敏）。
"""

import re
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Literal

ActorType = Literal["human", "agent", "system"]
Result = Literal["success", "denied", "failed"]
ACTOR_TYPES: frozenset[str] = frozenset({"human", "agent", "system"})
RESULTS: frozenset[str] = frozenset({"success", "denied", "failed"})

MAX_DETAIL_KEYS = 16
MAX_KEY_LENGTH = 64
MAX_VALUE_LENGTH = 256
MAX_LIST_ITEMS = 16
REDACTED = "[已脱敏]"

# 键名含这些片段的一律不记：口令、令牌、密钥
_SECRET_KEY = re.compile(r"pass(word|wd)?|secret|token|authorization|cookie|credential|api_?key|private",
                         re.IGNORECASE)
# 键名里出现这些词（按驼峰与下划线切词）也不记：提示词、回答、正文、工具参数与输出
_CONTENT_WORDS: frozenset[str] = frozenset({"prompt", "answer", "content", "message", "messages", "text", "body",
                                            "document", "documents", "arguments", "input", "output", "query"})
_WORD = re.compile(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])")


def _sensitive_key(name: str) -> bool:
    if _SECRET_KEY.search(name):
        return True
    return any(word.lower() in _CONTENT_WORDS for word in _WORD.findall(name))


# 值里像凭据的片段：Bearer 令牌、本平台 API Key、常见厂商密钥、JWT、PEM 私钥
_SECRET_VALUE = re.compile(
    r"(?i:bearer\s+\S+)|\bak_[A-Za-z0-9_\-]{8,}|\bsk-[A-Za-z0-9_\-]{8,}|"
    r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{4,}\.[A-Za-z0-9_\-]*|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)


def _clean_value(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, list | tuple):
        return [_clean_value(item) for item in list(value)[:MAX_LIST_ITEMS] if not isinstance(item, dict | list)]
    text = _SECRET_VALUE.sub(REDACTED, str(value))
    return text[:MAX_VALUE_LENGTH]


def sanitize_detail(detail: dict[str, Any] | None) -> dict[str, Any]:
    """返回新的字典；嵌套字典不展开（审计摘要不需要层级，层级里最容易混进正文）。"""
    if not detail:
        return {}
    cleaned: dict[str, Any] = {}
    for key, value in detail.items():
        if len(cleaned) >= MAX_DETAIL_KEYS:
            break
        name = str(key)[:MAX_KEY_LENGTH]
        if _sensitive_key(name) or isinstance(value, dict):
            continue
        cleaned[name] = _clean_value(value)
    return cleaned


def _clip(value: str | None, limit: int) -> str | None:
    return None if value is None else str(value)[:limit]


def new_event_id() -> str:
    return f"al_{uuid.uuid4().hex}"


@dataclass(frozen=True)
class AuditEvent:
    workspace_id: str
    actor_type: ActorType
    action: str
    result: Result
    actor_id: str | None = None
    credential_type: str | None = None
    credential_id: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    status_code: int | None = None
    ip: str | None = None
    request_id: str | None = None
    session_id: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    id: str = field(default_factory=new_event_id)

    def __post_init__(self) -> None:
        if not self.workspace_id:
            raise ValueError("审计事件缺少 workspace_id")
        if self.actor_type not in ACTOR_TYPES:
            raise ValueError(f"非法的操作者类型：{self.actor_type!r}")
        if self.result not in RESULTS:
            raise ValueError(f"非法的审计结果：{self.result!r}")
        if not self.action:
            raise ValueError("审计事件缺少 action")

    def normalized(self) -> "AuditEvent":
        """限长并脱敏后的新事件；落库前一律经过这里。"""
        return replace(
            self, workspace_id=self.workspace_id[:64], action=self.action[:64],
            actor_id=_clip(self.actor_id, 64), credential_type=_clip(self.credential_type, 16),
            credential_id=_clip(self.credential_id, 64), resource_type=_clip(self.resource_type, 64),
            resource_id=_clip(self.resource_id, 200), ip=_clip(self.ip, 64), request_id=_clip(self.request_id, 128),
            session_id=_clip(self.session_id, 128), detail=sanitize_detail(self.detail),
        )

    def row(self) -> dict[str, Any]:
        event = self.normalized()
        return {"id": event.id, "workspace_id": event.workspace_id, "occurred_at": event.occurred_at,
                "actor_type": event.actor_type, "actor_id": event.actor_id,
                "credential_type": event.credential_type, "credential_id": event.credential_id,
                "action": event.action, "resource_type": event.resource_type, "resource_id": event.resource_id,
                "result": event.result, "status_code": event.status_code, "ip": event.ip,
                "request_id": event.request_id, "session_id": event.session_id, "detail": event.detail}
