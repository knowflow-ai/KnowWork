"""运行面板接口（系统设置里，只给系统管理员，API Key 一律拒绝）。

- GET    /api/system/jobs/overview         → 各池统计、worker 心跳、下游闸门占用、任务类型的中文名
- GET    /api/system/jobs                  → 任务列表（按状态、池、类型筛选，按创建时间倒序翻页）
- GET    /api/system/jobs/{id}             → 单个任务与各阶段
- POST   /api/system/jobs/{id}/cancel      → 取消（走业务的失败收尾）
- POST   /api/system/jobs/{id}/retry       → 立即重跑
- DELETE /api/system/jobs/{id}             → 删除失败或已取消的记录
"""

from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from ..auth.principal import Principal
from .admin import STATUSES, JobConflict, JobFilter, JobNotFound, JobRow, JobsAdmin

NOT_ADMIN = "只有系统管理员能查看运行面板"
NO_API_KEY = "运行面板只能在网页上使用"
NOT_FOUND = "任务不存在，可能已经被清理"

IsAdmin = Callable[[str], Awaitable[bool]]
Status = Literal[STATUSES]  # type: ignore[valid-type]


def _job(row: JobRow) -> dict:
    return asdict(row)


def build_router(admin: JobsAdmin, principal, is_admin: IsAdmin) -> APIRouter:
    router = APIRouter(prefix="/api/system/jobs", tags=["system"])
    Who = Annotated[Principal, Depends(principal)]

    async def system_admin(who: Who) -> str:
        if who.is_api_key:
            raise HTTPException(status_code=403, detail=NO_API_KEY)
        if not await is_admin(who.user_id):
            raise HTTPException(status_code=403, detail=NOT_ADMIN)
        return who.user_id

    Admin = Annotated[str, Depends(system_admin)]

    async def run(call):
        try:
            return await call
        except JobNotFound as error:
            raise HTTPException(status_code=404, detail=NOT_FOUND) from error
        except JobConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.get("/overview")
    async def overview(_: Admin) -> dict:
        found = await admin.overview()
        return {"pools": [asdict(p) for p in found.pools], "workers": [asdict(w) for w in found.workers],
                "gates": [asdict(g) for g in found.gates], "kinds": [asdict(k) for k in found.kinds]}

    @router.get("")
    async def list_jobs(_: Admin, status: Annotated[Status | None, Query()] = None,
                        pool: Annotated[str | None, Query(max_length=32)] = None,
                        kind: Annotated[str | None, Query(max_length=64)] = None,
                        cursor: Annotated[str | None, Query(max_length=500)] = None) -> dict:
        page = await run(admin.jobs(JobFilter(status=status, pool=pool, kind=kind), cursor=cursor))
        return {"items": [_job(row) for row in page.items], "next": page.next}

    @router.get("/{job_id}")
    async def detail(job_id: str, _: Admin) -> dict:
        job, stages = await run(admin.detail(job_id))
        return {**_job(job), "stages": [asdict(stage) for stage in stages]}

    @router.post("/{job_id}/cancel")
    async def cancel(job_id: str, actor: Admin) -> dict:
        return _job(await run(admin.cancel(actor, job_id)))

    @router.post("/{job_id}/retry")
    async def retry(job_id: str, _: Admin) -> dict:
        return _job(await run(admin.retry(job_id)))

    @router.delete("/{job_id}", status_code=204)
    async def remove(job_id: str, _: Admin) -> Response:
        await run(admin.delete(job_id))
        return Response(status_code=204)

    return router
