"""管理端「技能 → 最近运行」的接口，只有空间管理员能看（与「运行记录」同一口径）：

- `GET /api/workspaces/{workspace_id}/skills/{skill_id}/runs`：最近的运行，新的在前，按游标向下翻页；
  可按时间段（since，默认最近 30 天）、状态、版本筛选；
- `GET .../runs/summary`：同一时间段内按版本汇总。
API Key 需要范围 skills:read。
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from ..access.deps import require_permission
from ..access.service import AccessService
from ..auth.principal import Principal
from .api import SCOPE_READ, IdPath
from .errors import SkillError
from .run_query import RunFilter, SkillRunQuery

DEFAULT_RANGE = timedelta(days=30)


def _since(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC) - DEFAULT_RANGE
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _readable(who: Principal) -> None:
    if not who.allows(SCOPE_READ):
        raise HTTPException(status_code=403, detail={"code": "forbidden_scope", "message": "API Key 缺少所需范围"})


async def _call(awaitable):
    try:
        return await awaitable
    except SkillError as error:
        raise HTTPException(status_code=error.status, detail=error.detail()) from error
    except ValueError as error:  # 翻页游标被改坏
        raise HTTPException(status_code=400, detail={"code": "invalid_request", "message": "翻页参数无效"}) \
            from error


def build_runs_router(query: SkillRunQuery, access: AccessService,
                      current_principal: Callable[..., Awaitable[Principal]]) -> APIRouter:
    router = APIRouter(prefix="/api/workspaces/{workspace_id}/skills/{skill_id}/runs", tags=["skills"])
    Admin = Annotated[Principal, Depends(require_permission(access, current_principal, lambda p: "workspace", "edit"))]

    @router.get("")
    async def runs(workspace_id: IdPath, skill_id: IdPath, who: Admin, since: datetime | None = None,
                   status: Literal["succeeded", "failed", "cancelled"] | None = None,
                   version: Annotated[str | None, Query(max_length=64)] = None,
                   before: Annotated[str | None, Query(max_length=200)] = None,
                   limit: Annotated[int, Query(ge=1, le=100)] = 20):
        _readable(who)
        page = await _call(query.recent(workspace_id, skill_id, RunFilter(since=_since(since), status=status,
                                                                           version=version),
                                        before=before, limit=limit))
        return {"items": [row.to_json() for row in page.items], "next": page.next}

    @router.get("/summary")
    async def summary(workspace_id: IdPath, skill_id: IdPath, who: Admin, since: datetime | None = None):
        _readable(who)
        versions = await _call(query.summary(workspace_id, skill_id, _since(since)))
        return {"versions": [item.to_json() for item in versions]}

    return router
