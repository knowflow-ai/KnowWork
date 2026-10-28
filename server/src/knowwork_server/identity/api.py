"""身份的公开接口：登录登出、当前用户、邀请注册、API Key、OIDC 单点登录。

邀请与查找账号按工作空间（/api/workspaces/{ws}/invitations、/api/workspaces/{ws}/users），
「是不是这个空间的管理员」由调用方注入（身份模块不认识空间）；默认谁都不是。
"""

import inspect
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response

from ..audit.context import annotate
from ..auth.principal import Principal
from .container import Identity, unauthorized
from .errors import EmailTaken, InvalidInput, InvalidInvitation, NotAllowed
from .hooks import Placement
from .oidc_routes import add_oidc_routes
from .schemas import AcceptInviteBody, CreateApiKeyBody, InviteBody, LoginBody, api_key_view
from .sessions import IssuedSession
from .throttle import account_key, ip_key
from .users import MAX_QUERY_LENGTH, MAX_SEARCH_LIMIT

LOGIN_FAILED = "邮箱或密码错误"
TOO_MANY_ATTEMPTS = "尝试次数过多，请稍后再试"

# 调用者在某个工作空间里是否满足要求：管理员（邀请、查找账号），或成员（把新 Key 绑定到这个空间）。
WorkspaceCheck = Callable[[Principal, str], bool | Awaitable[bool]]
WorkspacePath = Annotated[str, Path(pattern=r"^[A-Za-z0-9_.\-]{1,64}$")]


def set_session_cookie(response: Response, identity: Identity, issued: IssuedSession) -> None:
    config = identity.config
    response.set_cookie(config.session_cookie_name, issued.token, expires=issued.expires_at, path="/",
                        httponly=True, secure=config.cookie_secure, samesite="lax")


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _user_view(user) -> dict:
    return {"id": user.id, "email": user.email, "name": user.name}


def _no_workspace(_: Principal, __: str) -> bool:
    return False


async def _holds(check: WorkspaceCheck, principal: Principal, workspace_id: str) -> bool:
    result = check(principal, workspace_id)
    return bool(await result) if inspect.isawaitable(result) else bool(result)


def build_router(identity: Identity, *, is_admin: WorkspaceCheck = _no_workspace,
                 can_bind_workspace: WorkspaceCheck = _no_workspace) -> APIRouter:
    router = APIRouter(prefix="/api/auth")
    workspace = APIRouter(prefix="/api/workspaces/{workspace_id}")
    Who = Annotated[Principal, Depends(identity.get_principal)]

    async def admin(principal: Principal, workspace_id: str) -> bool:
        return await _holds(is_admin, principal, workspace_id)

    def human(principal: Principal) -> Principal:
        if principal.is_api_key:  # 管理 Key 与邀请必须由本人在浏览器里操作，Key 不能再签发 Key
            raise HTTPException(status_code=403, detail="该操作不支持使用 API Key")
        return principal

    @router.post("/login")
    async def login(body: LoginBody, request: Request, response: Response):
        """账号密码登录"""
        keys = (account_key(body.email), ip_key(_client_ip(request)))
        annotate(account=body.email.strip().lower()[:320])  # 登录失败时审计据此知道是哪个账号被试
        if identity.throttle.blocked(keys):
            raise HTTPException(status_code=429, detail=TOO_MANY_ATTEMPTS)
        user = await identity.users.authenticate(body.email, body.password)
        if user is None:
            identity.throttle.record_failure(keys)
            raise HTTPException(status_code=401, detail=LOGIN_FAILED)
        identity.throttle.reset(keys[:1])  # 只清账号计数；IP 计数不因任一账号登录成功而清零
        annotate(actor_id=user.id, resource_id=user.id)
        set_session_cookie(response, identity, await identity.sessions.create(user.id))
        return _user_view(user)

    @router.post("/logout", status_code=204)
    async def logout(request: Request, response: Response):
        """退出登录"""
        token = request.cookies.get(identity.config.session_cookie_name)
        principal = await identity.sessions.resolve(token) if token else None
        if principal is not None:
            annotate(actor_id=principal.user_id, resource_id=principal.user_id)
        if principal is not None and principal.session_id:
            await identity.sessions.revoke(principal.session_id)
        response.delete_cookie(identity.config.session_cookie_name, path="/", httponly=True,
                               secure=identity.config.cookie_secure, samesite="lax")

    @router.get("/me")
    async def me(who: Who):
        """当前登录的用户"""
        user = await identity.users.get(who.user_id)
        if user is None:
            raise unauthorized()
        scopes = sorted(who.scopes) if who.scopes is not None else None
        return {**_user_view(user), "authMethod": who.auth_method, "scopes": scopes}

    @workspace.post("/invitations", status_code=201)
    async def invite(workspace_id: WorkspacePath, body: InviteBody, who: Who):
        """邀请成员"""
        try:
            issued = await identity.invitations.create(
                email=body.email, invited_by=who.user_id, placement=Placement(workspace_id, body.role),
                caller_is_admin=await admin(human(who), workspace_id))
        except NotAllowed as error:
            raise HTTPException(status_code=403, detail="无权邀请用户") from error
        except InvalidInput as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except EmailTaken as error:
            raise HTTPException(status_code=409, detail="该邮箱已注册") from error
        return {"id": issued.id, "email": issued.email, "role": body.role, "url": issued.url,
                "expiresAt": issued.expires_at.isoformat()}

    @workspace.delete("/invitations/{invitation_id}", status_code=204)
    async def revoke_invitation(workspace_id: WorkspacePath, invitation_id: str, who: Who):
        """撤销邀请"""
        try:
            revoked = await identity.invitations.revoke(invitation_id, workspace_id=workspace_id,
                                                        caller_is_admin=await admin(human(who), workspace_id))
        except NotAllowed as error:
            raise HTTPException(status_code=403, detail="无权撤销邀请") from error
        if not revoked:
            raise HTTPException(status_code=404, detail="邀请不存在或已失效")

    @router.post("/invitations/accept", status_code=201)
    async def accept(body: AcceptInviteBody, response: Response):
        """接受邀请并设置密码"""
        try:
            accepted = await identity.invitations.accept(token=body.token, name=body.name, password=body.password)
        except InvalidInput as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except InvalidInvitation as error:
            raise HTTPException(status_code=400, detail="邀请无效或已过期") from error
        set_session_cookie(response, identity, accepted.session)
        annotate(actor_id=accepted.user.id)
        return _user_view(accepted.user)

    @router.get("/api-keys")
    async def list_keys(who: Who):
        """列出我的 API Key"""
        return [api_key_view(info) for info in await identity.api_keys.list(human(who).user_id)]

    @router.post("/api-keys", status_code=201)
    async def create_key(body: CreateApiKeyBody, who: Who):
        """新建 API Key（密钥只在这次返回）"""
        if not await _holds(can_bind_workspace, human(who), body.workspace_id):
            raise HTTPException(status_code=403, detail="不能把 API Key 绑定到你不属于的工作空间")
        try:
            issued = await identity.api_keys.create(user_id=human(who).user_id, name=body.name, scopes=body.scopes,
                                                    expires_at=body.expires_at, workspace_id=body.workspace_id)
        except InvalidInput as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        annotate(resource_id=issued.info.id, scopes=list(issued.info.scopes), boundWorkspace=body.workspace_id)
        return {**api_key_view(issued.info), "key": issued.plaintext}

    @router.delete("/api-keys/{key_id}", status_code=204)
    async def revoke_key(key_id: str, who: Who):
        """删除 API Key"""
        if not await identity.api_keys.revoke(user_id=human(who).user_id, key_id=key_id):
            raise HTTPException(status_code=404, detail="API Key 不存在或已吊销")

    @workspace.get("/users")
    async def search_users(workspace_id: WorkspacePath, who: Who,
                           q: Annotated[str, Query(min_length=1, max_length=MAX_QUERY_LENGTH)],
                           limit: Annotated[int, Query(ge=1, le=MAX_SEARCH_LIMIT)] = 20):
        """按邮箱或姓名搜索全部账号，给这个空间的管理员加成员时选人用；不暴露账号状态等其它字段。"""
        if not await admin(human(who), workspace_id):
            raise HTTPException(status_code=403, detail="无权查找用户")
        return [_user_view(user) for user in await identity.users.search(q, limit=limit)]

    add_oidc_routes(router, identity, set_session_cookie)
    combined = APIRouter()
    combined.include_router(router)
    combined.include_router(workspace)
    return combined
