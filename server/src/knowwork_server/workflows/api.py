"""固定工作流的公开 HTTP 接口，前缀 `/api/workspaces/{workspace_id}/workflow-runs`。

- 路由层要求空间成员（workspace use）；运行者本人、技能可用性由服务层判定；
- API Key 需要范围 workflows:run；
- 启动立即返回 202 与运行快照，之后用 GET 或事件流 `/events`（SSE，事件名 workflow.run，id 为 version）看进度。
"""

from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field
from sse_starlette.sse import EventSourceResponse

from ..access.deps import require_permission
from ..access.service import AccessService
from ..auth.principal import Principal
from .errors import WorkflowError
from .events import parse_after
from .service import MAX_LIST, Actor, WorkflowService

SCOPE = "workflows:run"
IdPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9_.\-]{1,64}$")]
StepPath = Annotated[str, Path(pattern=r"^[a-z][a-z0-9_-]{0,47}$")]


class StartBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    skill_id: str = Field(alias="skillId", pattern=r"^[A-Za-z0-9_.\-]{1,64}$")
    inputs: dict[str, Any] = Field(default_factory=dict)
    session_id: str | None = Field(default=None, alias="sessionId", pattern=r"^[A-Za-z0-9_.:-]{1,128}$")


class DecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow: bool


def _actor(principal: Principal, workspace_id: str) -> Actor:
    if not principal.allows(SCOPE):
        raise HTTPException(status_code=403, detail={"code": "forbidden_scope", "message": "API Key 缺少所需范围"})
    return Actor(user_id=principal.user_id, workspace_id=workspace_id)


async def _call(awaitable):
    try:
        return await awaitable
    except WorkflowError as error:
        raise HTTPException(status_code=error.status, detail=error.detail()) from error


def build_router(service: WorkflowService, access: AccessService,
                 current_principal: Callable[..., Awaitable[Principal]]) -> APIRouter:
    router = APIRouter(prefix="/api/workspaces/{workspace_id}/workflow-runs", tags=["workflows"])
    member = require_permission(access, current_principal, lambda p: "workspace", "use")
    Who = Annotated[Principal, Depends(member)]

    @router.post("", status_code=202)
    async def start(workspace_id: IdPath, body: StartBody, who: Who):
        run = await _call(service.start(_actor(who, workspace_id), body.skill_id, body.inputs,
                                        session_id=body.session_id))
        return run.to_json()

    @router.get("")
    async def list_runs(workspace_id: IdPath, who: Who, limit: Annotated[int, Query(ge=1, le=MAX_LIST)] = 20):
        runs = await _call(service.list(_actor(who, workspace_id), limit))
        return {"items": [run.to_json() for run in runs]}

    @router.get("/{run_id}")
    async def detail(workspace_id: IdPath, run_id: IdPath, who: Who):
        return (await _call(service.get(_actor(who, workspace_id), run_id))).to_json()

    @router.post("/{run_id}/steps/{step_key}/decision")
    async def decide(workspace_id: IdPath, run_id: IdPath, step_key: StepPath, body: DecisionBody, who: Who):
        return (await _call(service.decide(_actor(who, workspace_id), run_id, step_key, body.allow))).to_json()

    @router.post("/{run_id}/resume")
    async def resume(workspace_id: IdPath, run_id: IdPath, who: Who):
        return (await _call(service.resume(_actor(who, workspace_id), run_id))).to_json()

    @router.post("/{run_id}/cancel")
    async def cancel(workspace_id: IdPath, run_id: IdPath, who: Who):
        return (await _call(service.cancel(_actor(who, workspace_id), run_id))).to_json()

    @router.get("/{run_id}/events")
    async def events(workspace_id: IdPath, run_id: IdPath, who: Who, after: Annotated[int, Query(ge=0)] = 0,
                     last_event_id: Annotated[str | None, Header()] = None):
        stream = await _call(service.events(_actor(who, workspace_id), run_id, parse_after(last_event_id, after)))
        return EventSourceResponse(stream, ping=15)

    return router
