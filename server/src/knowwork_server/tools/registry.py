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
# 回答方式（同 kernel/contract.TurnInput.answer_mode）：工具声明自己在哪几种里提供，默认只在智能体里
ANSWER_MODES: frozenset[str] = frozenset({"agent", "quick"})
AGENT_ONLY: frozenset[str] = frozenset({"agent"})
AGENT_AND_QUICK: frozenset[str] = ANSWER_MODES
MAX_GUIDANCE = 1000


@dataclass(frozen=True)
class ToolContext:
    """一次调用的操作者上下文，来自已校验的令牌，不接受宿主或模型自报。

    agent_session_id 例外：是宿主报的「实际发起调用的会话」（子 Agent 用根会话的令牌，这里是子会话的 id），
    只用来区分是不是根会话自己发起的（例如检索去重只看根会话这一轮的上下文），不参与任何授权判断。"""

    workspace_id: str
    session_id: str
    user_id: str  # 会话所属用户；知识等按调用者身份过滤的工具依赖它
    agent_session_id: str | None = None
    # 宿主给的这次工具调用编号（同会话日志里的 callId）：只用来把业务记录（例如检索记录）对上运行详情里的那一步
    call_id: str | None = None

    @property
    def from_root(self) -> bool:
        """这次调用是根会话自己发起的（不是子 Agent）。没报发起方时按根会话算。"""
        return self.agent_session_id in (None, self.session_id)


@dataclass(frozen=True)
class ToolSpec:
    """交给宿主的工具描述。input_schema 必须是 DSH 官方支持的 JSON Schema 子集，对象根。

    - risk 是真实风险等级，给审计、展示与工作流缓存判断用，不为了少弹审批而调低；
    - approval 决定执行前是否走官方人工审批：不写时按 risk 推导（只读不问，写入与高风险要问），
      隔离由别处负责的工具（例如沙箱里的命令）显式写 "never"；
    - guidance 是写进系统提示的使用规则（可选），只在这个工具对该会话可见时才出现；
    - concurrent：模型一步里发了几个调用时可以同时执行（宿主照 DSH 官方声明 isConcurrencySafe）。只给只读、
      不改共享状态的工具（知识检索这类），写入工具与外部 MCP 工具不开，它们的并发安全我们保证不了；
    - answer_modes：在哪几种回答方式下提供（默认只在智能体里）。快速问答只给能「快速查知识库作答」的工具（只读知识库工具、
      用户明确要记的记忆写入），由定义工具的模块自己声明，和 risk、concurrent 一样随描述交给宿主；宿主按它算官方条件工具的
      `when`，名单不写在宿主里，加工具不用两头改；
    - tailor：按会话裁剪交给宿主的那份描述（可选），例如去掉这个会话用不上的参数。DSH 的工具参数注册时就定了，
      没有按步改参数的扩展点；官方自己的做法是装配时定（bash 只在有沙箱执行器时才带 sandbox_permissions）。
      宿主每个会话开始时拉一次清单，就在那时定，整个会话不变，不动模型服务的前缀缓存。只影响交给宿主的描述，
      执行时仍按完整的 input_schema 校验：会话中途情况变了，宿主手里那份描述照样能用。
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    risk: Risk = "read"
    approval: Approval | None = None
    guidance: str | None = None
    concurrent: bool = False
    answer_modes: frozenset[str] = AGENT_ONLY
    tailor: "Tailor | None" = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        if self.risk not in RISKS:
            raise ValueError(f"工具 {self.name} 的风险等级不合法")
        if self.approval is not None and self.approval not in APPROVALS:
            raise ValueError(f"工具 {self.name} 的审批方式不合法")
        if self.guidance is not None and not 0 < len(self.guidance) <= MAX_GUIDANCE:
            raise ValueError(f"工具 {self.name} 的使用规则为空或过长")
        if self.concurrent and self.risk != "read":
            raise ValueError(f"工具 {self.name} 不是只读的，不能声明同时执行")
        if not self.answer_modes or not self.answer_modes <= ANSWER_MODES:
            raise ValueError(f"工具 {self.name} 的回答方式不合法")

    @property
    def approval_mode(self) -> Approval:
        if self.approval is not None:
            return self.approval
        return "never" if self.risk == "read" else "ask"

    def describe(self) -> dict[str, Any]:
        described = {"name": self.name, "description": self.description, "inputSchema": self.input_schema,
                     "risk": self.risk, "approval": self.approval_mode}
        return {**described, **({"guidance": self.guidance} if self.guidance is not None else {}),
                **({"concurrent": True} if self.concurrent else {}),
                **({"answerModes": sorted(self.answer_modes)} if self.answer_modes != AGENT_ONLY else {})}


Tailor = Callable[[ToolContext, ToolSpec], Awaitable[ToolSpec]]


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
        concurrent=True,
    ),
    handler=_now,
)


def builtin_registry() -> StaticToolRegistry:
    return StaticToolRegistry.of([KW_NOW])
