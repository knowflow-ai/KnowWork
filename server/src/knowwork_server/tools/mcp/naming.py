"""MCP 工具对模型的名字与默认风险等级。

名字照搬 DSH 官方 dsh-mcp-client 的 publicToolName（lib/index.js）：`mcp__<服务名>__<工具原名>`，
按 DeepSeek 函数名约束（最多 64 字符、只含 `[A-Za-z0-9_-]`）规范化；规范化改动了名字时，
末尾补「服务名 \\0 原名」的 SHA-256 前 12 位，不同工具不会撞名。以后若改用官方插件，名字保持不变，
会话历史与授权规则都不受影响。原名只在调用 MCP 服务时用，从不从对外名字里反推。

服务名不允许下划线：`mcp__<服务名>__` 这个前缀因此不会歧义，按服务授权可以写成 `tool:mcp__<服务名>__*`
（Casbin keyMatch 前缀通配）。服务名最长 24，前缀最长 31，补哈希后的名字也保留完整前缀。
"""

import hashlib
import re
from typing import Any

from ..registry import Risk

MAX_PUBLIC_NAME_LENGTH = 64
HASH_LENGTH = 12
INVALID_NAME_CHARS = re.compile(r"[^A-Za-z0-9_-]")
SERVER_NAME_PATTERN = r"^[a-z][a-z0-9-]{0,23}$"
PREFIX = "mcp__"


def public_tool_name(server_name: str, raw_name: str) -> str:
    joined = f"{PREFIX}{server_name}__{raw_name}"
    normalized = INVALID_NAME_CHARS.sub("_", joined)
    if normalized == joined and len(normalized) <= MAX_PUBLIC_NAME_LENGTH:
        return normalized
    identity = hashlib.sha256(f"{server_name}\0{raw_name}".encode()).hexdigest()[:HASH_LENGTH]
    return f"{normalized[:MAX_PUBLIC_NAME_LENGTH - HASH_LENGTH - 1]}_{identity}"


def server_tool_pattern(server_name: str) -> str:
    """按服务授权用的工具名通配：覆盖这个服务现在与以后的全部工具。"""
    return f"{PREFIX}{server_name}__*"


def is_mcp_tool(name: str) -> bool:
    return name.startswith(PREFIX)


def annotated_risk(annotations: dict[str, Any] | None) -> Risk:
    """按 MCP 工具注解给默认风险等级：声明只读的是 read，明确声明有破坏性的是 dangerous，其余是 write。

    MCP 规范里 destructiveHint 缺省为 true，照字面会把所有没写注解的工具都算成高风险；这里只认明确的声明，
    没写注解的按写入处理（同样要审批）。管理员可以逐个工具改。
    """
    hints = annotations or {}
    if hints.get("readOnlyHint") is True:
        return "read"
    if hints.get("destructiveHint") is True:
        return "dangerous"
    return "write"
