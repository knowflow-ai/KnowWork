"""工具桥的内部路由，只挂在内部端口（宿主回调用）。

宿主插件回调统一走 `/internal/extensions/{plugin}/{operation}`：
- `GET  /internal/extensions/tools/list`：本会话可用的工具描述；
- `POST /internal/extensions/tools/execute`：执行一次调用，头 `Idempotency-Key` = 实际会话 ID:调用 ID；
- `POST /internal/extensions/tools/approval-grant`：这个工具在本会话是否已有「不再询问」的授权（宿主审批应答者
  弹卡片前来问）。对话与用户取自令牌，不接受宿主自报。

两者都要 `Authorization: Bearer <受众 tools 的短期令牌>`。错误统一为 `{"error": {"code", "message"}}`，
message 可以直接给模型看，不含内部细节。

登记了原生应用（桌面端）时，同一套接口在公开端口的 `/api/extensions/tools` 下再挂一份，给用户电脑上的本机宿主回调：
令牌是同一种，经令牌交换拿到（identity/oauth_exchange.py），没有令牌的请求一律 401。
"""

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..kernel.contract import ANSWER_MODE_PATTERN
from .service import ExecuteRequest, ToolAccessError, ToolService

PREFIX = "/internal/extensions/tools"
PUBLIC_PREFIX = "/api/extensions/tools"
# (workspace_id, conversation_id, user_id, tool_name) → 是否有会话授权（approvals.SessionGrants.covers）
GrantCheck = Callable[[str, str, str, str], Awaitable[bool]]


class _GrantBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    tool_name: str = Field(alias="toolName", pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


class _ExecuteBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    tool_name: str = Field(alias="toolName", pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    arguments: dict[str, Any] = Field(default_factory=dict)
    agent_session_id: str = Field(alias="agentSessionId", pattern=r"^[A-Za-z0-9_.:-]{1,128}$")
    call_id: str = Field(alias="callId", min_length=1, max_length=200)
    # 这一步的回答方式；老的宿主不带，按智能体算
    answer_mode: str = Field(default="agent", alias="answerMode", pattern=ANSWER_MODE_PATTERN)


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def _bearer(authorization: str | None) -> str:
    if authorization is None or not authorization.startswith("Bearer "):
        raise ToolAccessError(401, "unauthorized", "令牌无效")
    return authorization[7:]


def build_internal_router(service: ToolService, grants: GrantCheck | None = None, *,
                          path_prefix: str = PREFIX) -> APIRouter:
    router = APIRouter(include_in_schema=False)

    @router.post(f"{path_prefix}/approval-grant")
    async def approval_grant(request: Request, authorization: str | None = Header(default=None)):
        try:
            claims = service.verify(_bearer(authorization))
            try:
                body = _GrantBody.model_validate(await request.json())
            except (ValueError, ValidationError) as error:
                raise ToolAccessError(400, "invalid_request", "请求体格式错误") from error
        except ToolAccessError as error:
            return _error(error.status, error.code, str(error))
        granted = grants is not None and await grants(claims.workspace_id, claims.session_id, claims.user_id,
                                                      body.tool_name)
        return {"granted": granted}

    @router.get(f"{path_prefix}/list")
    async def list_tools(authorization: str | None = Header(default=None)):
        try:
            claims = service.verify(_bearer(authorization))
            specs = await service.list_tools(claims)
        except ToolAccessError as error:
            return _error(error.status, error.code, str(error))
        notes = await service.notes(claims)
        return {"tools": [spec.describe() for spec in specs], **({"notes": notes} if notes else {})}

    @router.post(f"{path_prefix}/execute")
    async def execute(request: Request, authorization: str | None = Header(default=None),
                      idempotency_key: str | None = Header(default=None)):
        try:
            claims = service.verify(_bearer(authorization))
            body = await _parse(request)
            if idempotency_key != f"{body.agent_session_id}:{body.call_id}":
                raise ToolAccessError(400, "invalid_request", "Idempotency-Key 必须是「会话 ID:调用 ID」")
            result = await service.execute(claims, ExecuteRequest(
                tool_name=body.tool_name, arguments=body.arguments, agent_session_id=body.agent_session_id,
                call_key=idempotency_key, answer_mode=body.answer_mode))
        except ToolAccessError as error:
            return _error(error.status, error.code, str(error))
        return {"actionId": result.action_id, "status": result.status, "content": result.content,
                "truncated": result.truncated, "resultRef": result.result_ref, "replayed": result.replayed,
                **({"presentation": result.presentation} if result.presentation is not None else {})}

    return router


async def _parse(request: Request) -> _ExecuteBody:
    try:
        return _ExecuteBody.model_validate(await request.json())
    except (ValueError, ValidationError) as error:
        raise ToolAccessError(400, "invalid_request", "请求体格式错误") from error
