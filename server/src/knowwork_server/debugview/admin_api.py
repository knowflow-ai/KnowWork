"""管理端「运行记录」的接口（要空间 owner 或 admin；查看、导出别人的运行会记审计）：

- GET /api/workspaces/{ws}/runs?since&until&user&status&signal&before&limit                   全空间每一轮，新的在前
  （signal：uncited 检索了知识库却没引用，disliked 被点了踩，unanswered 回合结束后判断知识库没答上来；tool：用过这个工具；base：检索到这个知识库，全局 id）
- GET /api/workspaces/{ws}/runs/{conversation_id}/turns/{turn}                                运行详情（同会话本人看到的）
- GET /api/workspaces/{ws}/runs/{conversation_id}/turns/{turn}/steps/{step}/input             某一步的输入
- GET /api/workspaces/{ws}/runs/{conversation_id}/turns/{turn}/export                         原始日志下载
"""

from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.responses import JSONResponse

from ..auth.principal import Principal
from ..conversations.service import ConversationNotFound
from .admin import Forbidden, Invalid, RunRecords
from .api import camel

IdPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9_.\-]{1,128}$")]
DEFAULT_RANGE = timedelta(days=7)


def _aware(value: datetime | None) -> datetime | None:
    return value if value is None or value.tzinfo is not None else value.replace(tzinfo=UTC)


async def _call(awaitable):
    try:
        return await awaitable
    except Forbidden as error:
        raise HTTPException(status_code=403, detail="只有空间管理员能查看运行记录") from error
    except Invalid as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except ConversationNotFound as error:
        raise HTTPException(status_code=404, detail="会话不存在") from error


def _found(value, message: str = "这一轮的记录不存在"):
    if value is None:
        raise HTTPException(status_code=404, detail=message)
    return value


def build_admin_router(records: RunRecords, current_principal: Callable[..., Awaitable[Principal]]) -> APIRouter:
    router = APIRouter(prefix="/api/workspaces/{workspace_id}/runs", tags=["debug"])
    Who = Annotated[Principal, Depends(current_principal)]

    @router.get("")
    async def runs(workspace_id: IdPath, who: Who, since: datetime | None = None, until: datetime | None = None,
                   before: datetime | None = None, user: str | None = None,
                   status: Annotated[str | None, Query(pattern="^(running|completed|cancelled|failed)$")] = None,
                   signal: Annotated[str | None, Query(pattern="^(uncited|disliked|unanswered)$")] = None,
                   tool: Annotated[str | None, Query(max_length=128)] = None,
                   base: Annotated[str | None, Query(max_length=200)] = None,
                   limit: Annotated[int, Query(ge=1, le=100)] = 50):
        """运行记录（管理员）"""
        end = _aware(until) or datetime.now(UTC)
        start = _aware(since) or end - DEFAULT_RANGE
        page = await _call(records.runs(who.user_id, workspace_id, since=start, until=end, before=_aware(before),
                                        user_id=user, status=status, signal=signal, tool=tool, base=base,
                                        limit=limit))
        return camel(asdict(page))

    @router.get("/{conversation_id}/turns/{turn}")
    async def turn(workspace_id: IdPath, conversation_id: IdPath, turn: int, who: Who):
        """运行记录：一轮的详情"""
        found = await _call(records.turn(who.user_id, workspace_id, conversation_id, turn))
        return camel(asdict(_found(found)))

    @router.get("/{conversation_id}/turns/{turn}/steps/{step}/input")
    async def step_input(workspace_id: IdPath, conversation_id: IdPath, turn: int, step: int, who: Who):
        """运行记录：一步的输入"""
        found = await _call(records.step_input(who.user_id, workspace_id, conversation_id, turn, step))
        return camel(asdict(_found(found, "这一步的记录不存在")))

    @router.get("/{conversation_id}/turns/{turn}/export")
    async def export(workspace_id: IdPath, conversation_id: IdPath, turn: int, who: Who):
        """运行记录：下载原始日志"""
        events = _found(await _call(records.export(who.user_id, workspace_id, conversation_id, turn)))
        name = f"{conversation_id}-turn-{turn}.json"
        return JSONResponse({"conversationId": conversation_id, "turn": turn, "events": events},
                            headers={"Content-Disposition": f'attachment; filename="{name}"'})

    return router
