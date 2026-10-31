"""工具的使用权判定，编译期与执行期共用同一个判定。

口径（与 access/rules.py 的默认策略、以及现状一致）：
- 先要是空间成员（workspace:use）；
- **内置工具**（kw_now、knowledge_search、knowledge_read 这类随服务发布、对所有成员开放的工具）不需要
  tool 授权：现状里所有成员都能用它们，而默认策略只给 owner、admin 授了 `tool:*`，若内置工具也要授权，
  编辑者与查看者升级后会突然失去它们。知识工具另按知识库授权（knowledge_base 的 use）收窄范围；
  扩展提供的工具（tools/provided.py，例如记忆写入）算内置工具，它们只碰调用者自己的数据，给不给由扩展按会话判断；
- **其余工具**（以后接入的 HTTP、MCP 工具）需要 `tool:<工具名>` 的 use 权限；默认只有 owner、admin
  （`tool:*`）能用，其他人靠显式授权。工具表上线前资源 id 就是工具名。
- **企业 MCP 的工具**（mcp__ent_ 开头）不在这里判：到了空间默认全体成员可用，空间停用、收窄后按授权，
  都由 MCP 模块（tools/mcp/enterprise.McpReach）在列工具与执行前判，这里放行，免得没收窄时也要逐个空间去授权。
判定出错一律当作没有权限。
"""

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from ..tools.provided import ProvidedTools
from ..tools.registry import ToolContext, ToolFailure, ToolRegistry, ToolSpec

log = logging.getLogger(__name__)

TOOL_RESOURCE = "tool"
# 企业 MCP 工具名的前缀，和 tools/mcp/naming.is_enterprise_tool 一致（测试钉住）。不 import 那个包：
# 它的 __init__ 会带进知识模块，知识模块又引用这里，成了循环引用
ENTERPRISE_MCP_PREFIX = "mcp__ent_"


class AccessChecker(Protocol):
    def check(self, user_id: str, workspace_id: str, resource: str, action: str) -> bool: ...


@dataclass(frozen=True)
class ToolClearance:
    access: AccessChecker
    builtin: frozenset[str]
    provided: ProvidedTools | None = None  # 扩展装配时才登记，所以按名字现查，不在构造时取一份

    def member(self, user_id: str, workspace_id: str) -> bool:
        return self._check(user_id, workspace_id, "workspace")

    def permits(self, user_id: str, workspace_id: str, name: str) -> bool:
        if not self.member(user_id, workspace_id):
            return False
        return self._builtin(name) or self._check(user_id, workspace_id, f"{TOOL_RESOURCE}:{name}")

    def permitted(self, user_id: str, workspace_id: str, names: list[str]) -> frozenset[str]:
        if not self.member(user_id, workspace_id):
            return frozenset()
        return frozenset(n for n in names if self._builtin(n) or self._check(user_id, workspace_id, f"{TOOL_RESOURCE}:{n}"))

    def _builtin(self, name: str) -> bool:
        return name in self.builtin or (self.provided is not None and self.provided.has(name)) \
            or name.startswith(ENTERPRISE_MCP_PREFIX)

    def builtin_names(self) -> frozenset[str]:
        """内置工具与扩展提供的工具的名字（运行配置里标 builtin 用）。"""
        return self.builtin | (self.provided.names() if self.provided is not None else frozenset())

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
