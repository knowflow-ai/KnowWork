"""把身份（谁）与权限（能做什么）接成各模块要的调用者依赖。

开源版只有默认工作空间：调用者必须是默认空间的成员，才能使用对话等业务接口。
API Key 还要带齐所需范围；绑定了别的工作空间的 Key 不能用在默认空间。
"""

from collections.abc import Awaitable, Callable

from fastapi import HTTPException, Request

from .access.members import DEFAULT_WORKSPACE_ID
from .access.service import AccessService
from .access.vocab import WORKSPACE_RESOURCE, Action
from .auth.principal import Principal
from .conversations.api import Actor
from .identity.container import Identity

CONVERSATIONS_SCOPE = "conversations:use"
WORKSPACE_SCOPE = "workspace:manage"
MODELS_SCOPE = "models:manage"
KNOWLEDGE_SCOPE = "knowledge:manage"
TOOLS_SCOPE = "tools:manage"
NOT_A_MEMBER = "你还不是本工作空间的成员，请联系管理员"
WRONG_WORKSPACE = "该 API Key 绑定的不是这个工作空间"
ACCESS_UNAVAILABLE = "权限服务暂时不可用，请稍后重试"

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


def conversation_actor(identity: Identity, access: AccessService):
    """对话接口的调用者：已登录（API Key 须带 conversations:use 范围），且是默认空间成员。"""
    authenticate = identity.require_scopes(CONVERSATIONS_SCOPE)

    async def dependency(request: Request) -> Actor:
        principal = await authenticate(request)
        if _bound_elsewhere(principal, DEFAULT_WORKSPACE_ID):
            raise HTTPException(status_code=403, detail=WRONG_WORKSPACE)
        if not access.engine.healthy:
            raise HTTPException(status_code=503, detail=ACCESS_UNAVAILABLE)
        if not access.check(principal.user_id, DEFAULT_WORKSPACE_ID, WORKSPACE_RESOURCE, Action.USE):
            raise HTTPException(status_code=403, detail=NOT_A_MEMBER)
        return Actor(user_id=principal.user_id, workspace_id=DEFAULT_WORKSPACE_ID)

    return dependency


def workspace_admin(access: AccessService):
    """系统管理（邀请、撤销邀请）：开源版只有一个空间，由默认空间的 owner 与 admin 担任。

    商业版多空间时，系统管理员要换成独立的判定口径，不能沿用某个空间的角色。
    """

    def check(principal: Principal) -> bool:
        return access.check(principal.user_id, DEFAULT_WORKSPACE_ID, WORKSPACE_RESOURCE, Action.EDIT)

    return check


def workspace_member(access: AccessService):
    """API Key 能否绑定到某个工作空间：调用者必须是该空间成员。"""

    def check(principal: Principal, workspace_id: str) -> bool:
        return access.check(principal.user_id, workspace_id, WORKSPACE_RESOURCE, Action.USE)

    return check
