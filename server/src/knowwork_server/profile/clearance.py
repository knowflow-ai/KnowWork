"""工具的使用权判定，编译期与执行期共用同一个判定。

口径（与 access/rules.py 的默认策略、以及现状一致）：
- 先要是空间成员（workspace:use）；
- **内置工具**（kw_now、knowledge_search、knowledge_read 这类随服务发布、对所有成员开放的工具）不需要
  tool 授权：现状里所有成员都能用它们，而默认策略只给 owner、admin 授了 `tool:*`，若内置工具也要授权，
  编辑者与查看者升级后会突然失去它们。知识工具另按知识库授权（knowledge_base 的 use）收窄范围；
- **其余工具**（以后接入的 HTTP、MCP 工具）需要 `tool:<工具名>` 的 use 权限；默认只有 owner、admin
  （`tool:*`）能用，其他人靠显式授权。工具表上线前资源 id 就是工具名。
判定出错一律当作没有权限。
"""

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from ..tools.registry import ToolContext, ToolFailure, ToolRegistry, ToolSpec

log = logging.getLogger(__name__)

TOOL_RESOURCE = "tool"


class AccessChecker(Protocol):
    def check(self, user_id: str, workspace_id: str, resource: str, action: str) -> bool: ...


@dataclass(frozen=True)
class ToolClearance:
    access: AccessChecker
    builtin: frozenset[str]

    def member(self, user_id: str, workspace_id: str) -> bool:
        return self._check(user_id, workspace_id, "workspace")

    def permits(self, user_id: str, workspace_id: str, name: str) -> bool:
        if not self.member(user_id, workspace_id):
            return False
        return name in self.builtin or self._check(user_id, workspace_id, f"{TOOL_RESOURCE}:{name}")

    def permitted(self, user_id: str, workspace_id: str, names: list[str]) -> frozenset[str]:
        if not self.member(user_id, workspace_id):
            return frozenset()
        return frozenset(n for n in names if n in self.builtin or self._check(user_id, workspace_id, f"{TOOL_RESOURCE}:{n}"))

    def _check(self, user_id: str, workspace_id: str, resource: str) -> bool:
        try:
            return self.access.check(user_id, workspace_id, resource, "use") is True
        except Exception:
            log.exception("tool clearance check failed; denying")
            return False


class AuthorizedToolRegistry:
    """在注册表外面套一层按当前授权过滤：ToolService 列工具与执行前都会调 available，权限收回立即生效。"""

    def __init__(self, inner: ToolRegistry, clearance: ToolClearance) -> None:
        self._inner = inner
        self._clearance = clearance

    async def all_tools(self, ctx: ToolContext) -> list[ToolSpec]:
        """不经授权过滤的全部工具，只给编译器算 denied_tools 用。"""
        return await self._inner.available(ctx)

    async def available(self, ctx: ToolContext) -> list[ToolSpec]:
        specs = await self._inner.available(ctx)
        allowed = self._clearance.permitted(ctx.user_id, ctx.workspace_id, [s.name for s in specs])
        return [spec for spec in specs if spec.name in allowed]

    async def invoke(self, ctx: ToolContext, name: str, arguments: dict[str, Any]) -> str:
        if not self._clearance.permits(ctx.user_id, ctx.workspace_id, name):
            raise ToolFailure(f"工具 {name} 已不在本会话的可用范围内")
        return await self._inner.invoke(ctx, name, arguments)
