"""工具步骤：直接经工具服务调注册表，不经模型。

- 走 ToolService 的完整链路：工具此刻仍对运行者可用 → 参数按 Schema 校验 → 动作回执 → 执行 → 审计；
- 会话 ID 固定为 `wf.<运行 ID>`，调用键固定为 `wf.<运行 ID>:<步骤键>`，所以动作回执的幂等键
  只由「运行 ID + 步骤键」决定：续跑时同一步不变，已执行过的写入从回执重放，不会重复执行；
- 回执仍在「执行中」（崩溃时这一步正在执行）不算这一步失败，交给引擎把运行标为中断，稍后再续跑。
"""

import json
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator

from ..tools.registry import ToolSpec
from ..tools.service import ExecuteRequest, ToolAccessError, ToolService
from .errors import StepFailed, StepInterrupted

RETRIABLE_TOOL_CODES = frozenset({"action_in_progress"})
PERMISSION_CODES = frozenset({"tool_not_available", "unauthorized"})


@dataclass(frozen=True)
class StepContext:
    workspace_id: str
    user_id: str
    run_id: str
    step_key: str
    attempt: int = 1

    @property
    def tool_session(self) -> str:
        return f"wf.{self.run_id}"


@dataclass(frozen=True)
class StepResult:
    output: Any
    tool_name: str | None = None
    action_id: str | None = None


def schema_error(schema: dict[str, Any] | None, value: Any) -> str | None:
    if schema is None:
        return None
    error = next(iter(Draft202012Validator(schema).iter_errors(value)), None)
    return None if error is None else error.message[:300]


def _decode(content: str) -> Any:
    try:
        return json.loads(content)
    except ValueError:
        return content


class ToolStepRunner:
    def __init__(self, tools: ToolService) -> None:
        self._tools = tools

    async def specs(self, ctx: StepContext) -> dict[str, ToolSpec]:
        specs = await self._tools.available_specs(ctx.workspace_id, ctx.tool_session, user_id=ctx.user_id)
        return {spec.name: spec for spec in specs}

    async def run(self, ctx: StepContext, tool: str, arguments: dict[str, Any],
                  output_schema: dict[str, Any] | None) -> StepResult:
        token = await self._tools.issue_session_token(ctx.workspace_id, ctx.tool_session, user_id=ctx.user_id,
                                                      only=frozenset({tool}))
        request = ExecuteRequest(tool_name=tool, arguments=arguments, agent_session_id=ctx.tool_session,
                                 call_key=f"{ctx.tool_session}:{ctx.step_key}")
        try:
            result = await self._tools.execute(self._tools.verify(token), request)
        except ToolAccessError as error:
            if error.code in RETRIABLE_TOOL_CODES:
                raise StepInterrupted(error.code, "这一步的工具调用还在执行，稍后可以续跑") from error
            stage = "permission" if error.code in PERMISSION_CODES else "tool"
            raise StepFailed(error.code, str(error), stage) from error
        if result.status != "succeeded":
            raise StepFailed("tool_failed", result.content[:500] or f"工具 {tool} 执行失败", "tool")
        if result.truncated:
            raise StepFailed("tool_output_too_large", f"工具 {tool} 的结果过大，不能作为流程步骤的结果", "tool")
        output = _decode(result.content)
        problem = schema_error(output_schema, output)
        if problem is not None:
            raise StepFailed("output_schema_mismatch", f"工具 {tool} 的结果不符合步骤声明：{problem}", "tool")
        return StepResult(output=output, tool_name=tool, action_id=result.action_id)
