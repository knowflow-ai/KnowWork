"""工作空间成员、部门树、用户组与资源授权的 HTTP 接口。

调用者身份由主线注入（current_principal），这里不做登录。写接口的权限判定在 AccessService 里。
"""

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict
from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from pydantic import BaseModel, Field

from ..audit.context import annotate
from ..auth.principal import Principal
from .errors import AccessDenied, AccessError, Conflict, NotFound
from .service import AccessService
from .vocab import InvalidInput

log = logging.getLogger(__name__)


class Profile(Protocol):
    """成员资料：姓名与邮箱。由账号模块提供，本模块不读账号表。"""

    name: str
    email: str


# 按用户 id 批量取资料；查不到的 id 不出现在结果里
ProfileLookup = Callable[[Sequence[str]], Awaitable[Mapping[str, Profile]]]

_STATUS = {AccessDenied: 403, NotFound: 404, Conflict: 409, InvalidInput: 422}
IdPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9_.\-]{1,64}$")]


class MemberBody(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    role: str


class RoleBody(BaseModel):
    role: str


class DepartmentBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    parent_id: str | None = None


class DepartmentPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    parent_id: str | None = None
    move: bool = False  # 为真时按 parent_id 移动（None 表示移到根）


class DepartmentMemberBody(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    is_primary: bool = False


class GroupBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class UserBody(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)


class GrantBody(BaseModel):
    subject: str = Field(min_length=1, max_length=80)
    resource: str = Field(min_length=1, max_length=120)
    action: str


def _http(error: Exception) -> HTTPException:
    for kind, status in _STATUS.items():
        if isinstance(error, kind):
            return HTTPException(status_code=status, detail=str(error))
    return HTTPException(status_code=500, detail="服务暂时不可用")


async def _call(awaitable):
    try:
        return await awaitable
    except (AccessError, InvalidInput) as error:
        raise _http(error) from error


def build_router(service: AccessService, current_principal: Callable[..., Awaitable[Principal]], *,
                 profiles: ProfileLookup | None = None) -> APIRouter:
    """profiles 可选：给了就在成员列表里补上姓名与邮箱，取不到时这两项为空，不影响列表本身。"""
    router = APIRouter(prefix="/api/workspaces/{workspace_id}", tags=["access"])
    Who = Annotated[Principal, Depends(current_principal)]
    _members(router, service, Who, profiles)
    _departments(router, service, Who)
    _groups(router, service, Who)
    _grants(router, service, Who)
    return router


async def _with_profiles(members: Sequence[dict], profiles: ProfileLookup) -> list[dict]:
    try:
        found = await profiles(tuple(m["user_id"] for m in members))
    except Exception:
        log.warning("member profile lookup failed; returning members without names", exc_info=True)
        found = {}
    return [{**m, "name": getattr(found.get(m["user_id"]), "name", None),
             "email": getattr(found.get(m["user_id"]), "email", None)} for m in members]


def _members(router: APIRouter, service: AccessService, Who, profiles: ProfileLookup | None) -> None:
    @router.get("/members")
    async def list_members(workspace_id: IdPath, who: Who):
        members = [asdict(m) for m in await _call(service.list_members(who.user_id, workspace_id))]
        return members if profiles is None else await _with_profiles(members, profiles)

    @router.post("/members", status_code=201)
    async def add_member(workspace_id: IdPath, body: MemberBody, who: Who):
        annotate(resource_id=body.user_id, role=body.role)
        return asdict(await _call(service.add_member(who.user_id, workspace_id, body.user_id, body.role)))

    @router.patch("/members/{user_id}")
    async def set_role(workspace_id: IdPath, user_id: IdPath, body: RoleBody, who: Who):
        annotate(role=body.role)
        return asdict(await _call(service.set_member_role(who.user_id, workspace_id, user_id, body.role)))

    @router.delete("/members/{user_id}", status_code=204)
    async def remove_member(workspace_id: IdPath, user_id: IdPath, who: Who):
        await _call(service.remove_member(who.user_id, workspace_id, user_id))
        return Response(status_code=204)


def _departments(router: APIRouter, service: AccessService, Who) -> None:
    @router.get("/departments")
    async def tree(workspace_id: IdPath, who: Who):
        return [asdict(n) for n in await _call(service.department_tree(who.user_id, workspace_id))]

    @router.post("/departments", status_code=201)
    async def create(workspace_id: IdPath, body: DepartmentBody, who: Who):
        dept_id = await _call(service.create_department(who.user_id, workspace_id, body.name, body.parent_id))
        return {"id": dept_id}

    @router.patch("/departments/{dept_id}", status_code=204)
    async def update(workspace_id: IdPath, dept_id: IdPath, body: DepartmentPatch, who: Who):
        moved = {"parent_id": body.parent_id} if body.move else {}
        await _call(service.update_department(who.user_id, workspace_id, dept_id, name=body.name, **moved))
        return Response(status_code=204)

    @router.delete("/departments/{dept_id}", status_code=204)
    async def delete(workspace_id: IdPath, dept_id: IdPath, who: Who):
        await _call(service.delete_department(who.user_id, workspace_id, dept_id))
        return Response(status_code=204)

    @router.post("/departments/{dept_id}/members", status_code=204)
    async def add_member(workspace_id: IdPath, dept_id: IdPath, body: DepartmentMemberBody, who: Who):
        await _call(service.add_department_member(who.user_id, workspace_id, dept_id, body.user_id, body.is_primary))
        return Response(status_code=204)

    @router.delete("/departments/{dept_id}/members/{user_id}", status_code=204)
    async def remove_member(workspace_id: IdPath, dept_id: IdPath, user_id: IdPath, who: Who):
        await _call(service.remove_department_member(who.user_id, workspace_id, dept_id, user_id))
        return Response(status_code=204)


def _groups(router: APIRouter, service: AccessService, Who) -> None:
    @router.get("/groups")
    async def list_groups(workspace_id: IdPath, who: Who):
        return [asdict(g) for g in await _call(service.list_groups(who.user_id, workspace_id))]

    @router.post("/groups", status_code=201)
    async def create(workspace_id: IdPath, body: GroupBody, who: Who):
        return {"id": await _call(service.create_group(who.user_id, workspace_id, body.name))}

    @router.patch("/groups/{group_id}", status_code=204)
    async def rename(workspace_id: IdPath, group_id: IdPath, body: GroupBody, who: Who):
        await _call(service.rename_group(who.user_id, workspace_id, group_id, body.name))
        return Response(status_code=204)

    @router.delete("/groups/{group_id}", status_code=204)
    async def delete(workspace_id: IdPath, group_id: IdPath, who: Who):
        await _call(service.delete_group(who.user_id, workspace_id, group_id))
        return Response(status_code=204)

    @router.post("/groups/{group_id}/members", status_code=204)
    async def add_member(workspace_id: IdPath, group_id: IdPath, body: UserBody, who: Who):
        await _call(service.add_group_member(who.user_id, workspace_id, group_id, body.user_id))
        return Response(status_code=204)

    @router.delete("/groups/{group_id}/members/{user_id}", status_code=204)
    async def remove_member(workspace_id: IdPath, group_id: IdPath, user_id: IdPath, who: Who):
        await _call(service.remove_group_member(who.user_id, workspace_id, group_id, user_id))
        return Response(status_code=204)


def _grants(router: APIRouter, service: AccessService, Who) -> None:
    @router.get("/grants")
    async def list_grants(workspace_id: IdPath, who: Who, subject: str | None = None, resource: str | None = None):
        grants = await _call(service.list_grants(who.user_id, workspace_id, subject=subject, resource=resource))
        return [asdict(g) for g in grants]

    @router.post("/grants", status_code=201)
    async def grant(workspace_id: IdPath, body: GrantBody, who: Who):
        annotate(subject=body.subject, resource=body.resource, grantedAction=body.action)
        return asdict(await _call(service.grant(who.user_id, workspace_id, body.subject, body.resource, body.action)))

    @router.delete("/grants", status_code=204)
    async def revoke(workspace_id: IdPath, who: Who, subject: Annotated[str, Query(max_length=80)],
                     resource: Annotated[str, Query(max_length=120)]):
        await _call(service.revoke(who.user_id, workspace_id, subject, resource))
        return Response(status_code=204)

    @router.get("/me/visible")
    async def visible(workspace_id: IdPath, who: Who, resource_type: str, action: str = "use"):
        result = service.visible_resources(who.user_id, workspace_id, resource_type, action)
        return {"all": result.all_, "ids": sorted(result.ids)}

    @router.get("/me/check")
    async def check(workspace_id: IdPath, who: Who, resource: str, action: str = "use"):
        return {"allowed": service.check(who.user_id, workspace_id, resource, action)}
