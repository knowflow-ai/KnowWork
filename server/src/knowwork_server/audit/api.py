"""审计查询接口：`GET /api/workspaces/{workspace_id}/audit-logs`。

调用者由装配方给的依赖算出（主线用 principals.scoped_principal(identity, AUDIT_SCOPE)：API Key 必须带
audit:read，且没有绑定到别的空间）；这里再判定是该空间的 owner 或 admin（workspace:edit）。
权限服务不健康时 503，不放行（fail-closed）。
"""

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from ..access.service import AccessService
from ..access.vocab import WORKSPACE_RESOURCE, Action
from ..auth.principal import Principal
from .models import AuditLog
from .query import MAX_LIMIT, AuditFilter, AuditQuery, InvalidCursor

AUDIT_SCOPE = "audit:read"
FORBIDDEN = "只有工作空间的所有者或管理员可以查看审计日志"
UNAVAILABLE = "权限服务暂时不可用，请稍后重试"
IdPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9_.\-]{1,64}$")]
ActionQuery = Annotated[str | None, Query(max_length=64, pattern=r"^[a-z0-9_]+(\.[a-z0-9_]+)*(\.\*)?$")]
ActorQuery = Annotated[str | None, Query(alias="actorId", max_length=64)]
ActorTypeQuery = Annotated[Literal["human", "agent", "system"] | None, Query(alias="actorType")]
ResultQuery = Annotated[Literal["success", "denied", "failed"] | None, Query()]


def audit_view(row: AuditLog) -> dict:
    return {"id": row.id, "occurredAt": row.occurred_at.isoformat(), "actorType": row.actor_type,
            "actorId": row.actor_id, "credentialType": row.credential_type, "credentialId": row.credential_id,
            "action": row.action, "resourceType": row.resource_type, "resourceId": row.resource_id,
            "result": row.result, "statusCode": row.status_code, "ip": row.ip, "requestId": row.request_id,
            "sessionId": row.session_id, "detail": row.detail or {}}


def _aware(value: datetime | None, label: str) -> datetime | None:
    if value is not None and value.tzinfo is None:
        raise HTTPException(status_code=422, detail=f"{label} 必须带时区")
    return value


def build_router(query: AuditQuery, access: AccessService,
                 current_principal: Callable[..., Awaitable[Principal]]) -> APIRouter:
    router = APIRouter(prefix="/api/workspaces/{workspace_id}", tags=["audit"])
    Who = Annotated[Principal, Depends(current_principal)]

    @router.get("/audit-logs")
    async def list_logs(workspace_id: IdPath, who: Who, since: datetime | None = None, until: datetime | None = None,
                        actor_id: ActorQuery = None, actor_type: ActorTypeQuery = None, action: ActionQuery = None,
                        result: ResultQuery = None, limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 50,
                        cursor: Annotated[str | None, Query(max_length=400)] = None):
        """查审计日志"""
        if not access.engine.healthy:
            raise HTTPException(status_code=503, detail=UNAVAILABLE)
        if not access.check(who.user_id, workspace_id, WORKSPACE_RESOURCE, Action.EDIT):
            raise HTTPException(status_code=403, detail=FORBIDDEN)
        flt = AuditFilter(since=_aware(since, "since"), until=_aware(until, "until"), actor_id=actor_id,
                          actor_type=actor_type, action=action, result=result)
        try:
            page = await query.page(workspace_id, flt, limit=limit, cursor=cursor)
        except InvalidCursor as error:
            raise HTTPException(status_code=422, detail="游标无效") from error
        return {"items": [audit_view(row) for row in page.items], "next": page.next}

    return router
