"""扩展提供的工具（例如记忆写入）。扩展在业务服务启动时登记，之后只读；同 knowledge/provided.py 的形态。

和内置工具一样只要求是空间成员（profile/clearance.ToolClearance 认得它们），排在内置工具之后、MCP 工具之前：
重名时内置工具优先，扩展顶替不了内置工具，MCP 工具也顶替不了扩展的。

available 决定这个会话给不给这个工具（例如空间没开记忆、渠道账号不给），列工具与执行前都会问：宿主每个会话开始时拉一次
清单，会话中途关掉了也执行不了。
"""

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .registry import BuiltinTool, ToolContext, ToolFailure, ToolSpec

log = logging.getLogger(__name__)

_NAME = re.compile(r"^[a-z][a-z0-9_]{1,63}$")  # 不带 mcp__ 这类前缀，和 MCP 工具名分得开

Availability = Callable[[ToolContext], Awaitable[bool]]


@dataclass(frozen=True)
class ProvidedTool:
    tool: BuiltinTool
    available: Availability | None = None  # 不给时对所有会话都给


class ProvidedTools:
    """实现 ToolRegistry 协议，并进 CompositeToolRegistry。"""

    def __init__(self) -> None:
        self._items: dict[str, ProvidedTool] = {}

    def add(self, item: ProvidedTool) -> None:
        name = item.tool.spec.name
        if not _NAME.fullmatch(name) or "__" in name:
            raise ValueError(f"invalid provided tool name: {name!r}")
        if name in self._items:
            raise ValueError(f"tool already provided: {name}")
        self._items = {**self._items, name: item}

    def has(self, name: str) -> bool:
        return name in self._items

    def names(self) -> frozenset[str]:
        return frozenset(self._items)

    async def available(self, ctx: ToolContext) -> list[ToolSpec]:
        return [item.tool.spec for item in self._items.values() if await _offered(item, ctx)]

    async def invoke(self, ctx: ToolContext, name: str, arguments: dict[str, Any]) -> str:
        item = self._items.get(name)
        if item is None or not await _offered(item, ctx):
            raise ToolFailure(f"工具 {name} 现在不可用")
        return await item.tool.handler(ctx, arguments)


async def _offered(item: ProvidedTool, ctx: ToolContext) -> bool:
    """判断出错时不给：宁可这个会话少一个工具，也不让扩展的问题拖垮整张清单。"""
    if item.available is None:
        return True
    try:
        return await item.available(ctx)
    except Exception:
        log.exception("availability of provided tool %s failed for session %s", item.tool.spec.name, ctx.session_id)
        return False
