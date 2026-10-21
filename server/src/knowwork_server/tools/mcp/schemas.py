"""MCP 管理接口的入参与出参（字段用 camelCase）。出参不含请求头明文，只有名字与末四位。"""

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

from .catalog import CatalogTool
from .naming import SERVER_NAME_PATTERN, server_tool_pattern
from .store import McpServerRecord

MAX_HEADERS = 10
MAX_HEADER_VALUE = 4096
HEADER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,63}$")
# 由 HTTP 客户端与 MCP 协议自己管理的头，不许配置
RESERVED_HEADERS = frozenset({"host", "content-length", "content-type", "accept", "connection", "transfer-encoding",
                              "mcp-session-id", "mcp-protocol-version", "last-event-id"})
URL = re.compile(r"https?://[^\s/?#]+(/[^\s?#]*)?")


class _Model(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


def _title(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not 1 <= len(cleaned) <= 100:
        raise ValueError("名称长度须为 1 到 100 个字符")
    return cleaned


def _url(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if len(cleaned) > 1000 or not URL.fullmatch(cleaned):
        raise ValueError("地址必须是 http(s):// 开头的完整地址，且不带查询参数；凭据请放在请求头里")
    return cleaned


def _headers(value: dict[str, str] | None) -> dict[str, str] | None:
    if value is None:
        return None
    if len(value) > MAX_HEADERS:
        raise ValueError(f"请求头最多 {MAX_HEADERS} 个")
    cleaned: dict[str, str] = {}
    for name, raw in value.items():
        if not HEADER_NAME.match(name) or name.lower() in RESERVED_HEADERS:
            raise ValueError(f"请求头 {name[:64]} 的名字不合法或不允许配置")
        if name.lower() in {key.lower() for key in cleaned}:
            raise ValueError(f"请求头 {name} 重复")
        text = raw.strip()
        if not text or len(text) > MAX_HEADER_VALUE or any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
            raise ValueError(f"请求头 {name} 的值为空、过长或含控制字符")
        cleaned[name] = text
    return cleaned


class _ServerFields(_Model):
    """新建与修改共用的字段校验；修改时字段可以缺省（None 跳过校验）。"""

    @field_validator("title", check_fields=False)
    @classmethod
    def _check_title(cls, value: str | None) -> str | None:
        return _title(value)

    @field_validator("url", check_fields=False)
    @classmethod
    def _check_url(cls, value: str | None) -> str | None:
        return _url(value)

    @field_validator("headers", check_fields=False)
    @classmethod
    def _check_headers(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        return _headers(value)


class McpServerCreate(_ServerFields):
    name: str = Field(pattern=SERVER_NAME_PATTERN)
    title: str
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=60, ge=5, le=300)


class McpServerPatch(_ServerFields):
    title: str | None = None
    url: str | None = None
    headers: dict[str, str] | None = None  # 给出时整体替换；不给表示不改
    timeout_seconds: int | None = Field(default=None, ge=5, le=300)
    enabled: bool | None = None


class McpToolPatch(_Model):
    """None 不改；auto 恢复为按注解（风险等级）与风险等级（审批方式）推导。"""

    risk: Literal["read", "write", "dangerous", "auto"] | None = None
    approval: Literal["ask", "never", "auto"] | None = None
    enabled: bool | None = None


class McpToolView(_Model):
    name: str
    raw_name: str
    description: str
    input_schema: dict[str, Any]
    risk: str
    risk_overridden: bool
    approval: str
    approval_overridden: bool
    enabled: bool
    problem: str | None

    @classmethod
    def of(cls, tool: CatalogTool) -> "McpToolView":
        approval = tool.approval_override or ("never" if tool.risk == "read" else "ask")
        return cls(name=tool.name, raw_name=tool.raw_name, description=tool.description,
                   input_schema=tool.input_schema, risk=tool.risk, risk_overridden=tool.risk_override is not None,
                   approval=approval, approval_overridden=tool.approval_override is not None,
                   enabled=tool.enabled, problem=tool.problem)


class HeaderView(_Model):
    name: str
    last4: str


class McpServerView(_Model):
    id: str
    name: str
    title: str
    url: str
    headers: list[HeaderView]
    timeout_seconds: int
    enabled: bool
    tools: list[McpToolView]
    grant_pattern: str
    synced_at: datetime | None
    sync_error: str | None
    created_by: str
    updated_at: datetime | None

    @classmethod
    def of(cls, record: McpServerRecord) -> "McpServerView":
        return cls(id=record.id, name=record.name, title=record.title, url=record.url,
                   headers=[HeaderView(**item) for item in record.header_names],
                   timeout_seconds=record.timeout_seconds, enabled=record.enabled,
                   tools=[McpToolView.of(tool) for tool in record.tools],
                   grant_pattern=server_tool_pattern(record.name), synced_at=record.synced_at,
                   sync_error=record.sync_error, created_by=record.created_by, updated_at=record.updated_at)
