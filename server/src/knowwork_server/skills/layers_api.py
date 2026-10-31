"""空间管理员的「企业」页签：到达本空间的企业技能与宿主内置技能，可以在本空间停用、收窄给谁用（不能改）。

- `GET /api/workspaces/{workspace_id}/platform-skills`：列表，每项带 key（platform:<id> 或 bundled:<名字>）、
  是否停用、是否收窄，以及收窄时授权挂的资源（resource）；
- `POST .../platform-skills/disable`、`.../enable`：请求体 `{"key": ...}`；
- `PUT .../platform-skills/users`：请求体 `{"key": ..., "subjects": [...]}`，含全体成员（role:editor）就是不收窄。
都要求对全部技能有 owner（空间所有者、管理员），服务层判。API Key 需要范围 skills:read（读）或 skills:write（写）。
"""

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field

from ..access.deps import require_permission
from ..access.service import AccessService
from ..auth.principal import Principal
from .api import IdPath, actor_of, call_service
from .layers import UpperSkills


class KeyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=80)


class UsersBody(KeyBody):
    subjects: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(max_length=200)


def build_layers_router(layers: UpperSkills, access: AccessService,
                        current_principal: Callable[..., Awaitable[Principal]]) -> APIRouter:
    router = APIRouter(prefix="/api/workspaces/{workspace_id}/platform-skills", tags=["skills"])
    member = require_permission(access, current_principal, lambda p: "workspace", "use")
    Who = Annotated[Principal, Depends(member)]

    @router.get("")
    async def list_upper(workspace_id: IdPath, who: Who):
        """本空间的企业技能
        到达本空间的企业技能（系统管理员发来的）与内置技能，以及本空间有没有停用、有没有收窄给谁用。"""
        found = await call_service(layers.list(actor_of(who, workspace_id, write=False)))
        return {"skills": [skill.to_json() for skill in found]}

    @router.put("/users")
    async def set_users(workspace_id: IdPath, body: UsersBody, who: Who):
        """改企业技能在本空间给谁用
        选了全体成员（role:editor）就是不收窄；否则只有选中的对象和管理员能用，一个都不选就只有管理员。"""
        narrowed = await call_service(layers.set_users(actor_of(who, workspace_id, write=True), body.key,
                                                       body.subjects))
        return {"key": body.key, "narrowed": narrowed}

    for verb, off in (("disable", True), ("enable", False)):
        _toggle(router, layers, verb, off, Who)
    return router


def _toggle(router: APIRouter, layers: UpperSkills, verb: str, off: bool, Who) -> None:
    @router.post(f"/{verb}", status_code=204)
    async def toggle(workspace_id: IdPath, body: KeyBody, who: Who):
        """在本空间停用或启用企业技能、内置技能"""
        await call_service(layers.set_off(actor_of(who, workspace_id, write=True), body.key, off))
        return Response(status_code=204)
