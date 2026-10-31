"""技能的公开 HTTP 接口。调用者身份由主线注入（current_principal）。

两组路由，范围由路径决定，服务层再按范围过滤（空间路由碰不到个人技能，反之亦然）：
- 空间技能 `/api/workspaces/{workspace_id}/skills`：单个技能上的操作用 access.deps.require_permission 判定
  `skill:{skill_id}` 的 use / edit / owner；安装与列表要求空间成员，安装另要求对全部技能有 owner；
- 个人技能 `/api/workspaces/{workspace_id}/me/skills`：要求空间成员，服务层只认本人的技能。上传即发布，没有发布接口；
  列表里每个技能带最近一次提交（submission）与没生效的原因（inactive）；`/me/skills/available` 是我在对话里
  能用的全部技能（企业、空间、个人、内置）；提交到空间与审核的路由在 submissions_api。

安装与更新草稿的请求体是 ZIP 原始字节（Content-Type: application/zip），边读边计数，超过上限立即 413。
更新草稿与发布可以带 `If-Match: <draft_revision>` 做乐观并发。
API Key 需要范围 skills:read（读）或 skills:write（写）。
"""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from ..access.deps import require_permission
from ..access.service import AccessService
from ..audit.context import annotate
from ..auth.principal import Principal
from .errors import SkillError
from .service import SkillService
from .views import Actor

if TYPE_CHECKING:
    from . import Skills

SCOPE_READ = "skills:read"
SCOPE_WRITE = "skills:write"
ZIP_TYPES = ("application/zip", "application/x-zip-compressed", "application/octet-stream")
IdPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9_.\-]{1,64}$")]


class PublishBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str | None = Field(default=None, max_length=64)


class EnvBody(BaseModel):
    """要改的环境变量：名字 → 新值，空串或 null 是清掉；没列出的不变。"""

    model_config = ConfigDict(extra="forbid")

    values: dict[Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")], str | None] = Field(max_length=32)


def _http(error: SkillError) -> HTTPException:
    return HTTPException(status_code=error.status, detail=error.detail())


def actor_of(principal: Principal, workspace_id: str, *, write: bool) -> Actor:
    if not principal.allows(SCOPE_WRITE if write else SCOPE_READ):
        raise HTTPException(status_code=403, detail={"code": "forbidden_scope", "message": "API Key 缺少所需范围"})
    return Actor(user_id=principal.user_id, workspace_id=workspace_id)


def revision_of(if_match: str | None) -> int | None:
    if if_match is None:
        return None
    try:
        return int(if_match.strip().strip('"'))
    except ValueError as error:
        raise HTTPException(status_code=400, detail={"code": "invalid_request", "message": "If-Match 必须是草稿修订号"}) \
            from error


async def read_archive(request: Request, limit: int) -> bytes:
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


async def call_service(awaitable):
    try:
        return await awaitable
    except SkillError as error:
        raise _http(error) from error


def build_router(skills: "Skills", access: AccessService,
                 current_principal: Callable[..., Awaitable[Principal]]) -> APIRouter:
    router = APIRouter(prefix="/api/workspaces/{workspace_id}", tags=["skills"])

    def skill_permission(action: str):
        return require_permission(access, current_principal, lambda p: f"skill:{p['skill_id']}", action)

    service = skills.service
    member = require_permission(access, current_principal, lambda p: "workspace", "use")
    _workspace_collection(router, skills, member)
    _personal_collection(router, skills, member)
    _items(router, service, "/skills", "workspace", skill_permission)
    _items(router, service, "/me/skills", "personal", lambda _action: member)
    return router


def _install_route(router: APIRouter, service: SkillService, base: str, scope: str, Who) -> None:
    @router.post(base, status_code=201)
    async def install(workspace_id: IdPath, request: Request, who: Who):
        """新建技能"""
        actor = actor_of(who, workspace_id, write=True)
        archive = await read_archive(request, service.limits.max_archive_bytes)
        return (await call_service(service.install(actor, scope, archive))).to_json()


def _workspace_collection(router: APIRouter, skills: "Skills", member) -> None:
    Who = Annotated[Principal, Depends(member)]
    _install_route(router, skills.service, "/skills", "workspace", Who)

    @router.get("/skills")
    async def list_skills(workspace_id: IdPath, who: Who):
        """列出技能
        每个技能带在对话里没生效的原因（inactive：被同名的企业技能顶掉、认领的工具已被企业技能占用）。"""
        views = await call_service(skills.service.list(actor_of(who, workspace_id, write=False), "workspace"))
        inactive = await skills.delivery.catalog.inactive_workspace(workspace_id)
        return {"skills": [{**view.to_json(), "inactive": inactive.get(view.id)} for view in views]}


def _personal_collection(router: APIRouter, skills: "Skills", member) -> None:
    Who = Annotated[Principal, Depends(member)]
    _install_route(router, skills.service, "/me/skills", "personal", Who)

    @router.get("/me/skills")
    async def list_mine(workspace_id: IdPath, who: Who):
        """列出我的技能
        每个技能带最近一次提交到空间的记录，以及在对话里没生效的原因（被同名的空间技能顶掉、认领的工具已被占用）。"""
        actor = actor_of(who, workspace_id, write=False)
        views = await call_service(skills.service.list(actor, "personal"))
        latest = await call_service(skills.submissions.latest(actor))
        inactive = await skills.delivery.catalog.inactive_personal(workspace_id, actor.user_id)
        return {"skills": [{**view.to_json(), "submission": latest[view.id].to_json() if view.id in latest else None,
                            "inactive": inactive.get(view.id)} for view in views]}

    @router.get("/me/skills/available")
    async def available(workspace_id: IdPath, who: Who):
        """我能用的技能
        对话里能 @ 或会被自动用上的全部技能：授权给我的空间技能、我的个人技能、内置技能。"""
        actor = actor_of(who, workspace_id, write=False)
        found = await skills.available.for_user(workspace_id, actor.user_id)
        return {"skills": [skill.to_json() for skill in found]}


def _items(router: APIRouter, service: SkillService, base: str, scope: str, permission) -> None:
    item = f"{base}/{{skill_id}}"
    Reader = Annotated[Principal, Depends(permission("use"))]
    Editor = Annotated[Principal, Depends(permission("edit"))]
    Owner = Annotated[Principal, Depends(permission("owner"))]

    @router.get(item)
    async def detail(workspace_id: IdPath, skill_id: IdPath, who: Reader):
        """一个技能的详情"""
        view, releases = await call_service(service.get(actor_of(who, workspace_id, write=False), skill_id, scope=scope))
        return {**view.to_json(), "releases": [release.to_json() for release in releases]}

    @router.put(f"{item}/draft")
    async def replace_draft(workspace_id: IdPath, skill_id: IdPath, request: Request, who: Editor,
                            if_match: str | None = Header(default=None)):
        """保存技能草稿"""
        actor = actor_of(who, workspace_id, write=True)
        archive = await read_archive(request, service.limits.max_archive_bytes)
        return (await call_service(service.replace_draft(actor, skill_id, archive, scope=scope,
                                                  expected_revision=revision_of(if_match)))).to_json()

    for verb, enabled in (("enable", True), ("disable", False)):
        _toggle(router, service, f"{item}/{verb}", scope, enabled, Editor)

    if scope == "workspace":  # 个人技能上传即发布
        _publish_route(router, service, item, Owner)

    @router.put(f"{item}/env")
    async def set_env(workspace_id: IdPath, skill_id: IdPath, body: EnvBody, who: Owner):
        """填写技能的环境变量
        值只写不读，接口只回已经填了哪些名字（envFilled）；空串或 null 是清掉。技能在对话里加载以后，它跑的命令才带上。"""
        annotate(resource_id=skill_id, envNames=sorted(body.values))  # 只记名字，不记值
        view = await call_service(service.set_env(actor_of(who, workspace_id, write=True), skill_id, body.values,
                                                  scope=scope))
        return view.to_json()

    @router.delete(item, status_code=204)
    async def remove(workspace_id: IdPath, skill_id: IdPath, who: Owner):
        """删除技能"""
        await call_service(service.delete(actor_of(who, workspace_id, write=True), skill_id, scope=scope))
        return Response(status_code=204)


def _publish_route(router: APIRouter, service: SkillService, item: str, Owner) -> None:
    @router.post(f"{item}/publish", status_code=201)
    async def publish(workspace_id: IdPath, skill_id: IdPath, who: Owner, body: PublishBody | None = None,
                      if_match: str | None = Header(default=None)):
        """发布技能"""
        actor = actor_of(who, workspace_id, write=True)
        release = await call_service(service.publish(actor, skill_id, scope="workspace",
                                              version=body.version if body else None,
                                              expected_revision=revision_of(if_match)))
        return release.to_json()


def _toggle(router: APIRouter, service: SkillService, path: str, scope: str, enabled: bool, Editor) -> None:
    @router.post(path)
    async def toggle(workspace_id: IdPath, skill_id: IdPath, who: Editor):
        """启用或停用技能"""
        actor = actor_of(who, workspace_id, write=True)
        return (await call_service(service.set_enabled(actor, skill_id, enabled, scope=scope))).to_json()
