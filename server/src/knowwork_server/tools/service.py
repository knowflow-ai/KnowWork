"""工具桥的业务服务侧：签发会话令牌、列出可用工具、执行一次调用。

执行顺序：令牌（受众 tools）→ 工具在令牌的可用清单里 → 注册表**此刻**仍判定可用 → 参数按 Schema 校验
→ 占位动作回执（幂等）→ 执行 → 结算回执。任何一步不通过都不执行。
"""

import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any, Literal

from jsonschema import Draft202012Validator

from ..db.base import enter_workspace
from ..kernel.tokens import TokenError
from .receipts import ActionCall, ActionResult, ReceiptStore, digest
from .registry import ToolContext, ToolFailure, ToolRegistry, ToolSpec
from .tokens import ToolTokenClaims, ToolTokenSigner

log = logging.getLogger(__name__)

CALL_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,128}:[\x21-\x7e]{1,200}$")
MAX_ARGUMENTS_BYTES = 256 * 1024


class ToolAccessError(Exception):
    """拒绝这次调用。code 是稳定错误码，status 是 HTTP 状态，message 可以给模型看。"""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass(frozen=True)
class ExecuteRequest:
    tool_name: str
    arguments: dict[str, Any]
    agent_session_id: str
    call_key: str  # 宿主给出的「实际发起调用的会话 ID:调用 ID」


@dataclass(frozen=True)
class ToolExecution:
    """一次工具执行的审计摘要：不含参数与输出原文，只有参数摘要（回执里同一个 digest）。"""

    workspace_id: str
    session_id: str
    user_id: str
    agent_session_id: str
    tool_name: str
    risk: str | None
    outcome: Literal["succeeded", "failed", "denied"]
    action_id: str | None = None
    code: str | None = None
    arguments_digest: str | None = None


ToolAuditHook = Callable[[ToolExecution], Awaitable[None]]


class ToolService:
    def __init__(self, registry: ToolRegistry, signer: ToolTokenSigner, receipts: ReceiptStore, *,
                 token_ttl_seconds: int = 900, audit: ToolAuditHook | None = None) -> None:
        self._registry = registry
        self._signer = signer
        self._receipts = receipts
        self._ttl = token_ttl_seconds
        self._audit = audit  # 可选；回调失败只记日志，不影响这次调用

    async def discard_session(self, session_id: str) -> None:
        await self._receipts.discard_session(session_id)

    async def issue_session_token(self, workspace_id: str, session_id: str, *, user_id: str,
                                  only: frozenset[str] | None = None) -> str:
        """会话打开（或续期）时签发；可用工具名取注册表当时的判定。user_id 是会话所属用户。

        only 不为空时再与它取交集：受限子会话（固定工作流的 agent 步骤）只拿到该步声明的工具。
        """
        specs = await self.available_specs(workspace_id, session_id, user_id=user_id)
        names = [spec.name for spec in specs if only is None or spec.name in only]
        return self._signer.issue(workspace_id=workspace_id, session_id=session_id, user_id=user_id,
                                  tools=names, ttl_seconds=self._ttl)

    async def available_specs(self, workspace_id: str, session_id: str, *, user_id: str) -> list[ToolSpec]:
        """此刻对该用户可用的全部工具描述（含风险等级），供工作流判定缓存与收窄子会话。"""
        ctx = ToolContext(workspace_id=workspace_id, session_id=session_id, user_id=user_id)
        return await self._registry.available(ctx)

    def verify(self, token: str) -> ToolTokenClaims:
        """核验宿主回调的令牌；通过后这次回调就以令牌里的空间读写（工具回执、沙箱都走按空间的会话工厂）。"""
        try:
            claims = self._signer.verify(token)
        except TokenError as error:
            raise ToolAccessError(401, "unauthorized", "令牌无效") from error
        enter_workspace(claims.workspace_id)
        return claims

    async def list_tools(self, claims: ToolTokenClaims) -> list[ToolSpec]:
        specs = await self._registry.available(_context(claims))
        return [spec for spec in specs if spec.name in claims.tools]

    async def execute(self, claims: ToolTokenClaims, request: ExecuteRequest) -> ActionResult:
        ctx = _context(claims)
        try:
            spec = await self._available_spec(claims, ctx, request.tool_name)
        except ToolAccessError as error:
            await self._report(ctx, request, None, "denied", code=error.code)
            raise
        try:
            result = await self._execute(ctx, spec, request)
        except ToolAccessError as error:
            await self._report(ctx, request, spec.risk, "failed", code=error.code)
            raise
        except Exception:
            await self._report(ctx, request, spec.risk, "failed", code="internal_error")
            raise
        if not result.replayed:  # 重放不是新的动作，第一次执行时已经记过
            await self._report(ctx, request, spec.risk, result.status, action_id=result.action_id)
        return result

    async def _execute(self, ctx: ToolContext, spec: ToolSpec, request: ExecuteRequest) -> ActionResult:
        _validate_call(spec, request)
        call = ActionCall(workspace_id=ctx.workspace_id, session_id=ctx.session_id,
                          agent_session_id=request.agent_session_id,
                          idempotency_key=f"{ctx.session_id}|{request.call_key}", tool_name=spec.name,
                          arguments_digest=_arguments_digest(request.arguments))
        claim = await self._receipts.claim(call)
        if claim.kind == "replay":
            return claim.result
        if claim.kind == "conflict":
            raise ToolAccessError(409, "idempotency_conflict", "这个调用 ID 已用于另一个操作")
        if claim.kind == "in_progress":
            raise ToolAccessError(409, "action_in_progress", "同一调用正在执行，请稍后再试")
        call_id = request.call_key.removeprefix(f"{request.agent_session_id}:")
        status, content = await self._run(replace(ctx, agent_session_id=request.agent_session_id, call_id=call_id), spec,
                                          request.arguments)
        return await self._receipts.settle(call, claim.action_id, status, content)

    async def _report(self, ctx: ToolContext, request: ExecuteRequest, risk: str | None, outcome: str, *,
                      action_id: str | None = None, code: str | None = None) -> None:
        if self._audit is None:
            return
        try:
            digest_ = _arguments_digest(request.arguments)
            await self._audit(ToolExecution(
                workspace_id=ctx.workspace_id, session_id=ctx.session_id, user_id=ctx.user_id,
                agent_session_id=request.agent_session_id, tool_name=request.tool_name, risk=risk, outcome=outcome,
                action_id=action_id, code=code, arguments_digest=digest_))
        except Exception:
            log.exception("tool audit hook failed for %s in session %s", request.tool_name, ctx.session_id)

    async def _available_spec(self, claims: ToolTokenClaims, ctx: ToolContext, name: str) -> ToolSpec:
        if name not in claims.tools:
            raise ToolAccessError(403, "tool_not_available", f"工具 {name} 不在本会话的可用范围内")
        spec = next((s for s in await self._registry.available(ctx) if s.name == name), None)
        if spec is None:
            raise ToolAccessError(403, "tool_not_available", f"工具 {name} 已不在本会话的可用范围内")
        return spec

    async def _run(self, ctx: ToolContext, spec: ToolSpec, arguments: dict[str, Any]) -> tuple[str, str]:
        try:
            output = await self._registry.invoke(ctx, spec.name, arguments)
        except ToolFailure as failure:
            return "failed", str(failure)
        except Exception:  # 工具内部错误只记日志，不把细节交给模型
            log.exception("tool %s failed in session %s", spec.name, ctx.session_id)
            return "failed", f"工具 {spec.name} 执行失败"
        if not isinstance(output, str):
            log.error("tool %s returned non-string output", spec.name)
            return "failed", f"工具 {spec.name} 执行失败"
        return "succeeded", output


def _arguments_digest(arguments: dict[str, Any]) -> str:
    return digest(json.dumps(arguments, sort_keys=True, ensure_ascii=False))


def _context(claims: ToolTokenClaims) -> ToolContext:
    return ToolContext(workspace_id=claims.workspace_id, session_id=claims.session_id, user_id=claims.user_id)


def _validate_call(spec: ToolSpec, request: ExecuteRequest) -> None:
    if not CALL_KEY_PATTERN.match(request.call_key):
        raise ToolAccessError(400, "invalid_request", "幂等键格式错误")
    if not request.call_key.startswith(f"{request.agent_session_id}:"):
        raise ToolAccessError(400, "invalid_request", "幂等键与发起调用的会话不符")
    if len(json.dumps(request.arguments, ensure_ascii=False).encode()) > MAX_ARGUMENTS_BYTES:
        raise ToolAccessError(413, "arguments_too_large", "参数过大")
    error = next(iter(Draft202012Validator(spec.input_schema).iter_errors(request.arguments)), None)
    if error is not None:
        raise ToolAccessError(400, "invalid_arguments", f"参数不符合工具 {spec.name} 的定义：{error.message[:200]}")
