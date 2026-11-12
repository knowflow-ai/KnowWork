"""把身份（谁）与权限（能做什么）接成各模块要的调用者依赖。

对话等业务接口的路径里带着工作空间（/api/workspaces/{ws}/...），调用者必须是这个空间的成员（系统管理员任何空间都算）。
对外检索与 MCP 的路径里没有空间，按 API Key 绑定的空间判，所以这类 Key 必须绑定空间。
API Key 还要带齐所需范围；绑定了别的工作空间的 Key 不能用在这个空间。
"""

from collections.abc import Awaitable, Callable
from typing import Protocol

from fastapi import HTTPException, Request

from .access.members import DEFAULT_WORKSPACE_ID
from .access.service import AccessService
from .access.vocab import WORKSPACE_RESOURCE, Action, InvalidInput, validate_id
from .auth.principal import Principal
from .conversations.api import Actor, Embedded, Throttled
from .db.base import enter_workspace
from .identity.container import Identity
from .identity.hooks import Placement
from .net import client_ip

CONVERSATIONS_SCOPE = "conversations:use"
WORKSPACE_SCOPE = "workspace:manage"
MODELS_SCOPE = "models:manage"
KNOWLEDGE_SCOPE = "knowledge:manage"
KNOWLEDGE_READ_SCOPE = "knowledge:read"  # 对外检索接口与 MCP
TOOLS_SCOPE = "tools:manage"
NOT_A_MEMBER = "你还不是本工作空间的成员，请联系管理员"
WRONG_WORKSPACE = "该 API Key 绑定的不是这个工作空间"
UNBOUND_KEY = "这个接口要用绑定了工作空间的 API Key"
ACCESS_UNAVAILABLE = "权限服务暂时不可用，请稍后重试"
EMBED_NOT_ALLOWED = "网页嵌入里用不了这个功能"

PrincipalDependency = Callable[[Request], Awaitable[Principal]]


def _bound_elsewhere(principal: Principal, workspace_id: str) -> bool:
    return principal.workspace_id is not None and principal.workspace_id != workspace_id


def scoped_principal(identity: Identity, scope: str) -> PrincipalDependency:
    """给工作空间、模型、知识等管理路由用：API Key 必须带对应范围，且没有绑定到别的空间。

    各路由内部再按路径里的工作空间做 RBAC 判定，这里只把住凭证本身能做什么。
    """
    authenticate = identity.require_scopes(scope)

    async def dependency(request: Request) -> Principal:
        principal = await authenticate(request)
        workspace_id = request.path_params.get("workspace_id")
        if workspace_id is not None and _bound_elsewhere(principal, workspace_id):
            raise HTTPException(status_code=403, detail=WRONG_WORKSPACE)
        return principal

    return dependency


def bound_principal(identity: Identity) -> PrincipalDependency:
    """不检查范围（由路由自己按读写判），只拒绝绑定到别的空间的 API Key。"""

    async def dependency(request: Request) -> Principal:
        principal = await identity.get_principal(request)
        workspace_id = request.path_params.get("workspace_id")
        if workspace_id is not None and _bound_elsewhere(principal, workspace_id):
            raise HTTPException(status_code=403, detail=WRONG_WORKSPACE)
        return principal

    return dependency


async def path_workspace(request: Request) -> None:
    """公开应用的全局依赖：路径里带工作空间的请求，以它为当前空间读写（行级安全据此放行）。
    这里不判权限，谁能做什么由各路由自己判；空间编号格式不对时不设，由路由报 404 或 422。"""
    raw = request.path_params.get("workspace_id")
    if not raw:
        return
    try:
        enter_workspace(validate_id(raw, "workspace_id"))
    except InvalidInput:
        return


def _member_of(access: AccessService, principal: Principal, workspace_id: str) -> Actor:
    if _bound_elsewhere(principal, workspace_id):
        raise HTTPException(status_code=403, detail=WRONG_WORKSPACE)
    if not access.engine.healthy:
        raise HTTPException(status_code=503, detail=ACCESS_UNAVAILABLE)
    if not access.check(principal.user_id, workspace_id, WORKSPACE_RESOURCE, Action.USE):
        raise HTTPException(status_code=403, detail=NOT_A_MEMBER)
    enter_workspace(workspace_id)  # MCP 不走 FastAPI 的全局依赖，这里也设一次
    return Actor(user_id=principal.user_id, workspace_id=workspace_id, channel_id=principal.channel_id,
                 visitor=principal.visitor)


def conversation_actor(identity: Identity, access: AccessService, embedded: Embedded | None = None, *,
                       visitors: bool = True):
    """对话接口的调用者：已登录（API Key 须带 conversations:use 范围），且是路径里那个空间的成员。

    从网页嵌入进来的（channels/web.py）：路径里有会话 id 的，要是自己从这个渠道开的会话（公开访问按访客分：
    渠道账号是所有访客共用的），这一处管住会话、引用、附件、产物、调试这些带会话 id 的接口。visitors=False 给不按会话分、
    按人列的接口用（例如暂存附件）：公开访问的访客共用一个账号，放进来就能看到、删掉别人的。"""
    authenticate = identity.require_scopes(CONVERSATIONS_SCOPE)

    async def dependency(request: Request) -> Actor:
        principal = await authenticate(request)
        try:
            workspace_id = validate_id(request.path_params.get("workspace_id", ""), "workspace_id")
        except InvalidInput as error:
            raise HTTPException(status_code=404, detail="工作空间不存在") from error
        actor = _member_of(access, principal, workspace_id)
        if principal.is_embedded:
            if embedded is None or (actor.visitor is not None and not visitors):
                raise HTTPException(status_code=403, detail=EMBED_NOT_ALLOWED)
            conversation_id = request.path_params.get("conversation_id")
            if conversation_id is not None and not await embedded.owns(actor, conversation_id):
                raise HTTPException(status_code=404, detail="会话不存在")
        return actor

    return dependency


class EmbeddedActions(Protocol):
    """网页嵌入的会话（channels/web.py）里扩展的卡片操作要用的那两样。"""

    async def owns(self, actor: Actor, conversation_id: str) -> bool: ...

    async def act(self, actor: Actor, client_ip: str) -> None: ...


class EmbedGuard:
    """扩展里网页嵌入也能用的接口，碰某段对话前的核对（扩展上下文的 embed_guard）。

    例如问数结果卡片上的下载、改条件、下钻：按查询编号来，路径里没有会话 id，conversation_actor 管不到；扩展用
    principal(CONVERSATIONS_SCOPE) 让嵌入身份进来，查到这次查询属于哪段对话后调 check。不是嵌入身份的直接过（归属由扩展
    自己判）；嵌入身份要是自己从这个渠道开的会话（公开访问时渠道账号是所有访客共用的，按访客分，不是的像不存在一样 404），
    再按访客限次（429）。网页渠道是后建的（渠道模块装配在扩展之后），建好了 bind；没 bind 时嵌入身份一律 403。"""

    def __init__(self, access: AccessService) -> None:
        self._access = access
        self._embedded: EmbeddedActions | None = None

    def bind(self, embedded: EmbeddedActions) -> None:
        self._embedded = embedded

    async def check(self, principal: Principal, request: Request | None, conversation_id: str) -> None:
        if not principal.is_embedded:
            return
        if self._embedded is None or request is None:
            raise HTTPException(status_code=403, detail=EMBED_NOT_ALLOWED)
        workspace_id = request.path_params.get("workspace_id", "")
        actor = _member_of(self._access, principal, workspace_id)
        if not await self._embedded.owns(actor, conversation_id):
            raise HTTPException(status_code=404, detail="没有找到，或者不是这段对话里的")
        try:
            await self._embedded.act(actor, client_ip(request))
        except Throttled as error:
            raise HTTPException(status_code=429, detail=str(error)) from error


def knowledge_reader(identity: Identity, access: AccessService):
    """对外检索与 MCP 的调用者：API Key 要带 knowledge:read、绑定了空间，且是那个空间的成员；
    能查哪些知识库再按授权收窄。路径里没有空间（MCP 客户端只认一个固定地址），空间只能从 Key 上来。"""
    authenticate = identity.require_scopes(KNOWLEDGE_READ_SCOPE)

    async def dependency(request: Request) -> Actor:
        principal = await authenticate(request)
        if principal.workspace_id is None:
            raise HTTPException(status_code=403, detail=UNBOUND_KEY)
        return _member_of(access, principal, principal.workspace_id)

    return dependency


def workspace_admin(access: AccessService):
    """邀请、撤销邀请、查找账号：这个空间的所有者与管理员（系统管理员任何空间都算）。"""

    def check(principal: Principal, workspace_id: str) -> bool:
        return access.check(principal.user_id, workspace_id, WORKSPACE_RESOURCE, Action.EDIT)

    return check


def workspace_member(access: AccessService):
    """API Key 能否绑定到某个工作空间：调用者必须是该空间成员。"""

    def check(principal: Principal, workspace_id: str) -> bool:
        return access.check(principal.user_id, workspace_id, WORKSPACE_RESOURCE, Action.USE)

    return check


def place_new_user(access: AccessService):
    """建号后的去处：凭邀请注册的进邀请定的空间与角色；没有邀请的（单点登录、自助注册）进默认空间当成员。"""

    async def hook(user_id: str, placement: Placement | None) -> None:
        if placement is None:
            await access.join_workspace(user_id, DEFAULT_WORKSPACE_ID)
        else:
            await access.join_workspace(user_id, placement.workspace_id, placement.role)

    return hook
