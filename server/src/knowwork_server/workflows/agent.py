"""agent 步骤：一次受限的子会话。

- 经内核合同 open_session 打开一个独立会话：工具令牌只含该步声明的工具（allow），SessionPolicy.deny 收窄
  其余所有工具（宿主侧的第二道防线）；不下发技能令牌，子会话里没有技能；
- 给模型的是：步骤提示、步骤输入（JSON）、输出 JSON Schema；回答按 Schema 校验，不合格在同一会话里
  说明哪里不对再要一次，最多重试 max_retries 次（定义里上限 2）；
- 事件读取复用 conversations 的 events.v1 投影，只认「本轮完成 / 失败 / 取消」与助手消息；
- 子会话里出现工具审批时直接以失败结束：需要审批的写入应写成 tool 步骤，前面放一个 approval 步骤；
- 宿主重启、会话丢失、连不上宿主属于基础设施问题，不算这一步失败：抛 StepInterrupted，运行可续跑。
"""

import asyncio
import contextlib
import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import httpx

from ..conversations.events_v1 import ProjectionState, project
from ..kernel.contract import AgentKernel, KernelError, SessionPolicy, SessionSpec, TurnInput
from ..skills.workflow_def import AgentStep
from ..tools.service import ToolService
from .errors import StepFailed, StepInterrupted
from .steps import StepContext, StepResult, schema_error

log = logging.getLogger(__name__)

DEFAULT_EXTRA_DENY = ("skill", "subagent")  # DSH 内置工具：子会话不加载技能、不再派生子 Agent
FENCE = re.compile(r"^```(?:json)?\s*\n(.*)\n```$", re.DOTALL)
MAX_REPLY = 200_000
INFRA_ERRORS = (KernelError, httpx.HTTPError, OSError)


@dataclass(frozen=True)
class _Cursor:
    after: int = 0
    epoch: str | None = None


def compose_prompt(prompt: str, step_input: Any, schema: dict[str, Any]) -> str:
    parts = [prompt.strip()]
    if step_input is not None:
        parts.append("## 输入\n```json\n" + json.dumps(step_input, ensure_ascii=False, indent=2) + "\n```")
    parts.append("## 输出要求\n只输出一个 JSON 值，不要输出任何其他文字。它必须符合下面的 JSON Schema：\n"
                 "```json\n" + json.dumps(schema, ensure_ascii=False) + "\n```")
    return "\n\n".join(parts)


def parse_reply(text: str, schema: dict[str, Any]) -> tuple[Any, str | None]:
    """返回（值，问题）。问题为空表示合格。"""
    body = text.strip()
    fenced = FENCE.match(body)
    if fenced:
        body = fenced.group(1).strip()
    if not body:
        return None, "回答是空的"
    try:
        value = json.loads(body)
    except ValueError:
        return None, "回答不是合法的 JSON"
    problem = schema_error(schema, value)
    return value, problem


def _assistant_text(kind: str, data: Any) -> str:
    """助手消息的完整文本。不用 events.v1 投影里的 text：那里为前端截到 8000 字，JSON 结果会被截坏。"""
    if kind != "session/event" or not isinstance(data, dict) or data.get("type") != "assistant/message":
        return ""
    payload = data.get("data") if isinstance(data.get("data"), dict) else {}
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    content = message.get("content") if isinstance(message.get("content"), list) else []
    texts = [block.get("text", "") for block in content
             if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)]
    return "".join(texts)[:MAX_REPLY]


class KernelAgentRunner:
    def __init__(self, kernel: AgentKernel, tools: ToolService, *,
                 extra_deny: Iterable[str] = DEFAULT_EXTRA_DENY) -> None:
        self._kernel = kernel
        self._tools = tools
        self._extra_deny = tuple(extra_deny)

    async def run(self, ctx: StepContext, step: AgentStep, prompt: str, step_input: Any) -> StepResult:
        session_id = f"wf.{ctx.run_id}.{ctx.step_key}.{ctx.attempt}"
        spec = await self._spec(ctx, step, session_id)
        try:
            await self._kernel.open_session(spec)
        except INFRA_ERRORS as error:
            raise StepInterrupted("runtime_unavailable", "运行时暂时不可用，稍后可以续跑") from error
        try:
            async with asyncio.timeout(step.timeout_seconds):
                return StepResult(output=await self._converse(session_id, step, prompt, step_input))
        except TimeoutError as error:
            with contextlib.suppress(*INFRA_ERRORS):
                await self._kernel.cancel(session_id)
            raise StepFailed("agent_timeout", f"步骤超过 {step.timeout_seconds} 秒没有完成", "generation") from error
        finally:
            await self._dispose(session_id)

    async def _spec(self, ctx: StepContext, step: AgentStep, session_id: str) -> SessionSpec:
        allowed = frozenset(step.tools)
        names = {spec.name for spec in await self._tools.available_specs(ctx.workspace_id, session_id,
                                                                         user_id=ctx.user_id)}
        missing = sorted(allowed - names)
        if missing:
            raise StepFailed("tool_not_available", f"工具 {missing[0]} 不在你的可用范围内", "permission")
        deny = tuple(sorted((names | set(self._extra_deny)) - allowed))
        token = None
        if allowed:
            token = await self._tools.issue_session_token(ctx.workspace_id, session_id, user_id=ctx.user_id,
                                                          only=allowed)
        return SessionSpec(session_id=session_id, policy=SessionPolicy(deny=deny), tools_token=token)

    async def _converse(self, session_id: str, step: AgentStep, prompt: str, step_input: Any) -> Any:
        text, cursor, problem = compose_prompt(prompt, step_input, step.output_schema), _Cursor(), ""
        for _ in range(step.max_retries + 1):
            reply, cursor = await self._turn(session_id, text, cursor)
            value, problem = parse_reply(reply, step.output_schema)
            if problem is None:
                return value
            text = f"上一次的回答不合格：{problem}。请只输出一个符合输出要求里 JSON Schema 的 JSON 值。"
        raise StepFailed("output_schema_mismatch",
                         f"重试 {step.max_retries} 次后输出仍不符合步骤声明：{problem}", "generation")

    async def _turn(self, session_id: str, text: str, cursor: _Cursor) -> tuple[str, _Cursor]:
        try:
            await self._kernel.send(session_id, TurnInput(text=text[:200_000]))
            return await self._await_turn_end(session_id, cursor)
        except INFRA_ERRORS as error:
            raise StepInterrupted("runtime_unavailable", "运行时暂时不可用，稍后可以续跑") from error

    async def _await_turn_end(self, session_id: str, cursor: _Cursor) -> tuple[str, _Cursor]:
        state, reply = ProjectionState(), ""
        async for frame in self._kernel.subscribe(session_id, after=cursor.after, epoch=cursor.epoch):
            cursor = _Cursor(after=frame.cursor, epoch=frame.epoch)
            state, events = project(state, frame.kind, frame.data, seq=frame.cursor, time=frame.time)
            reply = _assistant_text(frame.kind, frame.data) or reply
            for event in events:
                kind = event["type"]
                if kind == "run.waiting":
                    raise StepFailed("approval_not_supported",
                                     "agent 步骤里的工具需要审批：请改成 tool 步骤并在前面放一个审批步骤", "permission")
                elif kind in ("run.failed", "run.cancelled"):
                    raise StepFailed("agent_turn_failed", "子会话这一轮没有正常完成", "generation")
                elif kind == "run.completed":
                    return reply, cursor
        raise StepInterrupted("runtime_stream_closed", "运行时的事件流中断，稍后可以续跑")

    async def _dispose(self, session_id: str) -> None:
        try:
            await self._kernel.dispose(session_id)
        except INFRA_ERRORS:  # 释放失败不影响步骤结果；宿主空闲回收会兜底
            log.warning("failed to dispose workflow sub-session %s", session_id)
