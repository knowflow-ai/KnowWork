"""技能的公开 HTTP 接口。调用者身份由主线注入（current_principal）。

两组路由，范围由路径决定，服务层再按范围过滤（空间路由碰不到个人技能，反之亦然）：
- 空间技能 `/api/workspaces/{workspace_id}/skills`：单个技能上的操作用 access.deps.require_permission 判定
  `skill:{skill_id}` 的 use / edit / owner；安装与列表要求空间成员，安装另要求对全部技能有 owner；
- 个人技能 `/api/workspaces/{workspace_id}/me/skills`：要求空间成员，服务层只认本人的技能。

安装与更新草稿的请求体是 ZIP 原始字节（Content-Type: application/zip），边读边计数，超过上限立即 413。
更新草稿与发布可以带 `If-Match: <draft_revision>` 做乐观并发。
API Key 需要范围 skills:read（读）或 skills:write（写）。
"""

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from ..access.deps import require_permission
from ..access.service import AccessService
from ..auth.principal import Principal
from .errors import SkillError
from .service import SkillService
from .views import Actor

SCOPE_READ = "skills:read"
SCOPE_WRITE = "skills:write"
ZIP_TYPES = ("application/zip", "application/x-zip-compressed", "application/octet-stream")
IdPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9_.\-]{1,64}$")]


class PublishBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str | None = Field(default=None, max_length=64)


def _http(error: SkillError) -> HTTPException:
    return HTTPException(status_code=error.status, detail=error.detail())


def _actor(principal: Principal, workspace_id: str, *, write: bool) -> Actor:
    if not principal.allows(SCOPE_WRITE if write else SCOPE_READ):
        raise HTTPException(status_code=403, detail={"code": "forbidden_scope", "message": "API Key 缺少所需范围"})
    return Actor(user_id=principal.user_id, workspace_id=workspace_id)


def _revision(if_match: str | None) -> int | None:
    if if_match is None:
        return None
    try:
        return int(if_match.strip().strip('"'))
    except ValueError as error:
        raise HTTPException(status_code=400, detail={"code": "invalid_request", "message": "If-Match 必须是草稿修订号"}) \
            from error


async def _archive(request: Request, limit: int) -> bytes:
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type not in ZIP_TYPES:
        raise HTTPException(status_code=415, detail={"code": "unsupported_media_type", "message": "请求体必须是 ZIP"})
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise HTTPException(status_code=413, detail={"code": "archive_too_large", "message": "ZIP 超过大小上限"})
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise HTTPException(status_code=413, detail={"code": "archive_too_large", "message": "ZIP 超过大小上限"})
        chunks.append(chunk)
    return b"".join(chunks)


async def _call(awaitable):
    try:
        return await awaitable
    except SkillError as error:
        raise _http(error) from error


def build_router(service: SkillService, access: AccessService,
                 current_principal: Callable[..., Awaitable[Principal]]) -> APIRouter:
    router = APIRouter(prefix="/api/workspaces/{workspace_id}", tags=["skills"])

    def skill_permission(action: str):
        return require_permission(access, current_principal, lambda p: f"skill:{p['skill_id']}", action)

    member = require_permission(access, current_principal, lambda p: "workspace", "use")
    _collection(router, service, member, "/skills", "workspace")
    _collection(router, service, member, "/me/skills", "personal")
    _items(router, service, "/skills", "workspace", skill_permission)
    _items(router, service, "/me/skills", "personal", lambda _action: member)
    return router


def _collection(router: APIRouter, service: SkillService, member, base: str, scope: str) -> None:
    Who = Annotated[Principal, Depends(member)]

    @router.post(base, status_code=201)
    async def install(workspace_id: IdPath, request: Request, who: Who):
        """新建技能"""
        actor = _actor(who, workspace_id, write=True)
        archive = await _archive(request, service.limits.max_archive_bytes)
        return (await _call(service.install(actor, scope, archive))).to_json()

    @router.get(base)
    async def list_skills(workspace_id: IdPath, who: Who):
        """列出技能"""
        views = await _call(service.list(_actor(who, workspace_id, write=False), scope))
        return {"skills": [view.to_json() for view in views]}


def _items(router: APIRouter, service: SkillService, base: str, scope: str, permission) -> None:
    item = f"{base}/{{skill_id}}"
    Reader = Annotated[Principal, Depends(permission("use"))]
    Editor = Annotated[Principal, Depends(permission("edit"))]
    Owner = Annotated[Principal, Depends(permission("owner"))]

    @router.get(item)
    async def detail(workspace_id: IdPath, skill_id: IdPath, who: Reader):
        """一个技能的详情"""
        view, releases = await _call(service.get(_actor(who, workspace_id, write=False), skill_id, scope=scope))
        return {**view.to_json(), "releases": [release.to_json() for release in releases]}

    @router.put(f"{item}/draft")
    async def replace_draft(workspace_id: IdPath, skill_id: IdPath, request: Request, who: Editor,
                            if_match: str | None = Header(default=None)):
        """保存技能草稿"""
        actor = _actor(who, workspace_id, write=True)
        archive = await _archive(request, service.limits.max_archive_bytes)
        return (await _call(service.replace_draft(actor, skill_id, archive, scope=scope,
                                                  expected_revision=_revision(if_match)))).to_json()

    for verb, enabled in (("enable", True), ("disable", False)):
        _toggle(router, service, f"{item}/{verb}", scope, enabled, Editor)

    @router.post(f"{item}/publish", status_code=201)
    async def publish(workspace_id: IdPath, skill_id: IdPath, who: Owner, body: PublishBody | None = None,
                      if_match: str | None = Header(default=None)):
        """发布技能"""
        actor = _actor(who, workspace_id, write=True)
        release = await _call(service.publish(actor, skill_id, scope=scope, version=body.version if body else None,
                                              expected_revision=_revision(if_match)))
        return release.to_json()

    @router.delete(item, status_code=204)
    async def remove(workspace_id: IdPath, skill_id: IdPath, who: Owner):
        """删除技能"""
        await _call(service.delete(_actor(who, workspace_id, write=True), skill_id, scope=scope))
        return Response(status_code=204)


def _toggle(router: APIRouter, service: SkillService, path: str, scope: str, enabled: bool, Editor) -> None:
    @router.post(path)
    async def toggle(workspace_id: IdPath, skill_id: IdPath, who: Editor):
        """启用或停用技能"""
        actor = _actor(who, workspace_id, write=True)
        return (await _call(service.set_enabled(actor, skill_id, enabled, scope=scope))).to_json()
