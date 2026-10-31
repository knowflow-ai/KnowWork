"""个人技能提交到空间与审核的接口。都要求空间成员；审核另要求对全部技能有 owner（服务层判）。

- 作者：`POST /me/skills/{skill_id}/submission` 提交（可带附言），`DELETE` 同一路径撤回待审的那份；
- 审核：`GET /skill-submissions` 待审列表，`GET /skill-submissions/{id}` 详情（含 SKILL.md 正文与空间现有那版），
  `GET .../archive` 下载提交的技能包，`POST .../approve` 通过（可直接发布），`POST .../reject` 不通过（要写原因）。
API Key 需要范围 skills:read（读）或 skills:write（写）。
"""

from collections.abc import Awaitable, Callable
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field

from ..access.deps import require_permission
from ..access.service import AccessService
from ..auth.principal import Principal
from .api import IdPath, actor_of, call_service
from .submissions import MAX_NOTE, SubmissionService


class SubmitBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = Field(default="", max_length=MAX_NOTE * 2)


class ApproveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    publish: bool = False


class RejectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(max_length=MAX_NOTE * 2)


def build_submissions_router(submissions: SubmissionService, access: AccessService,
                             current_principal: Callable[..., Awaitable[Principal]]) -> APIRouter:
    router = APIRouter(prefix="/api/workspaces/{workspace_id}", tags=["skills"])
    member = require_permission(access, current_principal, lambda p: "workspace", "use")
    Who = Annotated[Principal, Depends(member)]
    base = "/skill-submissions/{submission_id}"

    @router.post("/me/skills/{skill_id}/submission", status_code=201)
    async def submit(workspace_id: IdPath, skill_id: IdPath, who: Who, body: SubmitBody | None = None):
        """提交到空间
        提交的是这个个人技能当前的版本，空间管理员审核通过后复制成空间技能。已有待审的会被这次顶掉。"""
        actor = actor_of(who, workspace_id, write=True)
        return (await call_service(submissions.submit(actor, skill_id, body.note if body else ""))).to_json()

    @router.delete("/me/skills/{skill_id}/submission", status_code=204)
    async def withdraw(workspace_id: IdPath, skill_id: IdPath, who: Who):
        """撤回提交"""
        await call_service(submissions.withdraw(actor_of(who, workspace_id, write=True), skill_id))
        return Response(status_code=204)

    @router.get("/skill-submissions")
    async def pending(workspace_id: IdPath, who: Who):
        """待审核的技能"""
        found = await call_service(submissions.pending(actor_of(who, workspace_id, write=False)))
        return {"submissions": [view.to_json() for view in found]}

    @router.get(base)
    async def detail(workspace_id: IdPath, submission_id: IdPath, who: Who):
        """一份提交的详情
        带提交的 SKILL.md 正文；空间里已有从同一个个人技能来的那份时，带上它当前草稿的正文与清单，用来对比。"""
        found = await call_service(submissions.detail(actor_of(who, workspace_id, write=False), submission_id))
        return found.to_json()

    @router.get(f"{base}/archive")
    async def archive(workspace_id: IdPath, submission_id: IdPath, who: Who):
        """下载提交的技能包"""
        name, body = await call_service(submissions.archive(actor_of(who, workspace_id, write=False),
                                                            submission_id))
        disposition = f"attachment; filename*=UTF-8''{quote(name)}"
        return Response(content=body, media_type="application/zip", headers={"content-disposition": disposition})

    @router.post(f"{base}/approve")
    async def approve(workspace_id: IdPath, submission_id: IdPath, who: Who, body: ApproveBody | None = None):
        """通过提交
        第一次通过在空间里新建技能，以后是它的新版本；不直接发布时进草稿。"""
        actor = actor_of(who, workspace_id, write=True)
        view = await call_service(submissions.approve(actor, submission_id, publish=bool(body and body.publish)))
        return view.to_json()

    @router.post(f"{base}/reject")
    async def reject(workspace_id: IdPath, submission_id: IdPath, body: RejectBody, who: Who):
        """不通过提交"""
        actor = actor_of(who, workspace_id, write=True)
        return (await call_service(submissions.reject(actor, submission_id, body.reason))).to_json()

    return router
