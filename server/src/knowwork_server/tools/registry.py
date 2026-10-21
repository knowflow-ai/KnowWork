"""工具注册表接口与第一阶段的内置实现。

上层（ToolService）只依赖 `ToolRegistry` 协议：给定调用上下文，列出可用工具、执行一次调用。
内置工具用 StaticToolRegistry，MCP 工具见 tools/mcp，两者由 CompositeToolRegistry 并在一起；
权限判定（profile/clearance.AuthorizedToolRegistry）套在最外层。上层与宿主协议都不用改。
"""

import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

log = logging.getLogger(__name__)

Risk = Literal["read", "write", "dangerous"]
Approval = Literal["ask", "never"]
RISKS: frozenset[str] = frozenset({"read", "write", "dangerous"})
APPROVALS: frozenset[str] = frozenset({"ask", "never"})
MAX_GUIDANCE = 1000


@dataclass(frozen=True)
class ToolContext:
    """一次调用的操作者上下文，来自已校验的令牌，不接受宿主或模型自报。"""

    workspace_id: str
    session_id: str
    user_id: str  # 会话所属用户；知识等按调用者身份过滤的工具依赖它


@dataclass(frozen=True)
class ToolSpec:
    """交给宿主的工具描述。input_schema 必须是 DSH 官方支持的 JSON Schema 子集，对象根。

    - risk 是真实风险等级，给审计、展示与工作流缓存判断用，不为了少弹审批而调低；
    - approval 决定执行前是否走官方人工审批：不写时按 risk 推导（只读不问，写入与高风险要问），
      隔离由别处负责的工具（例如沙箱里的命令，照 WeKnora）显式写 "never"；
    - guidance 是写进系统提示的使用规则（可选），只在这个工具对该会话可见时才出现。
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    risk: Risk = "read"
    approval: Approval | None = None
    guidance: str | None = None

    def __post_init__(self) -> None:
        if self.risk not in RISKS:
            raise ValueError(f"工具 {self.name} 的风险等级不合法")
        if self.approval is not None and self.approval not in APPROVALS:
            raise ValueError(f"工具 {self.name} 的审批方式不合法")
        if self.guidance is not None and not 0 < len(self.guidance) <= MAX_GUIDANCE:
            raise ValueError(f"工具 {self.name} 的使用规则为空或过长")

    @property
    def approval_mode(self) -> Approval:
        if self.approval is not None:
            return self.approval
        return "never" if self.risk == "read" else "ask"

    def describe(self) -> dict[str, Any]:
        described = {"name": self.name, "description": self.description, "inputSchema": self.input_schema,
                     "risk": self.risk, "approval": self.approval_mode}
        return described if self.guidance is None else {**described, "guidance": self.guidance}


class ToolFailure(Exception):
    """工具执行失败。message 会原样交给模型，只能写面向用户的说明，不能带内部细节。"""


class ToolRegistry(Protocol):
    async def available(self, ctx: ToolContext) -> list[ToolSpec]: ...

    async def invoke(self, ctx: ToolContext, name: str, arguments: dict[str, Any]) -> str: ...


Handler = Callable[[ToolContext, dict[str, Any]], Awaitable[str]]


@dataclass(frozen=True)
class BuiltinTool:
    spec: ToolSpec
    handler: Handler


@dataclass
class StaticToolRegistry:
    """进程内固定的一组工具，对所有会话同样可用。第一阶段只有内置工具，用它即可。"""

    tools: dict[str, BuiltinTool] = field(default_factory=dict)

    @classmethod
    def of(cls, tools: Iterable[BuiltinTool]) -> "StaticToolRegistry":
        registry = cls()
        for tool in tools:
            if tool.spec.name in registry.tools:
                raise ValueError(f"工具重名：{tool.spec.name}")
            registry.tools[tool.spec.name] = tool
        return registry

    async def available(self, ctx: ToolContext) -> list[ToolSpec]:
        return [tool.spec for tool in self.tools.values()]

    async def invoke(self, ctx: ToolContext, name: str, arguments: dict[str, Any]) -> str:
        tool = self.tools.get(name)
        if tool is None:
            raise ToolFailure(f"工具 {name} 不存在")
        return await tool.handler(ctx, arguments)


class CompositeToolRegistry:
    """按顺序合并几个注册表；重名时前面的优先（内置工具放最前，外部工具不能顶替它）。"""

    def __init__(self, *registries: ToolRegistry) -> None:
        self._registries = registries

    async def available(self, ctx: ToolContext) -> list[ToolSpec]:
        return [spec for _, spec in await self._owned(ctx)]

    async def invoke(self, ctx: ToolContext, name: str, arguments: dict[str, Any]) -> str:
        owner = next((registry for registry, spec in await self._owned(ctx) if spec.name == name), None)
        if owner is None:
            raise ToolFailure(f"工具 {name} 不存在")
        return await owner.invoke(ctx, name, arguments)

    async def _owned(self, ctx: ToolContext) -> list[tuple[ToolRegistry, ToolSpec]]:
        owned: list[tuple[ToolRegistry, ToolSpec]] = []
        seen: set[str] = set()
        for registry in self._registries:
            for spec in await registry.available(ctx):
                if spec.name in seen:
                    log.warning("tool %s is shadowed by an earlier registry", spec.name)
                    continue
                seen.add(spec.name)
                owned.append((registry, spec))
        return owned


async def _now(_ctx: ToolContext, arguments: dict[str, Any]) -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


KW_NOW = BuiltinTool(
    spec=ToolSpec(
        name="kw_now",
        description="返回服务器的当前时间（UTC，ISO 8601）。",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        risk="read",
    ),
    handler=_now,
)


def builtin_registry() -> StaticToolRegistry:
    return StaticToolRegistry.of([KW_NOW])
