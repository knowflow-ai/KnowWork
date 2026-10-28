"""问答大盘的接口（只给空间所有者和管理员）：

- GET /api/workspaces/{ws}/insights?days=7|30|90   最近几天的大盘
"""

from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from ..auth.principal import Principal
from ..debugview.api import camel
from .service import Forbidden, InsightsService, Invalid

IdPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9_.\-]{1,64}$")]


def build_router(insights: InsightsService, current_principal: Callable[..., Awaitable[Principal]]) -> APIRouter:
    router = APIRouter(prefix="/api/workspaces/{workspace_id}/insights", tags=["insights"])
    Who = Annotated[Principal, Depends(current_principal)]

    @router.get("")
    async def overview(workspace_id: IdPath, who: Who, days: Annotated[int, Query()] = 7):
        """问答大盘"""
        try:
            found = await insights.overview(who.user_id, workspace_id, days)
        except Forbidden as error:
            raise HTTPException(status_code=403, detail="只有空间所有者和管理员能看问答大盘") from error
        except Invalid as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return camel(asdict(found))

    return router
