"""MCP 服务的管理接口（公开端口，管理后台「工具与 MCP」页用）。权限判定在 McpAdmin 里。

- GET    /api/workspaces/{ws}/mcp-servers                         列表（含工具清单快照）
- POST   /api/workspaces/{ws}/mcp-servers                         登记：先连一次、拉工具清单，连不上不保存
- GET    /api/workspaces/{ws}/mcp-servers/{id}
- PATCH  /api/workspaces/{ws}/mcp-servers/{id}                    改名称、地址、请求头、超时、启停
- DELETE /api/workspaces/{ws}/mcp-servers/{id}
- POST   /api/workspaces/{ws}/mcp-servers/{id}/sync               重新拉工具清单
- PATCH  /api/workspaces/{ws}/mcp-servers/{id}/tools/{tool}       改单个工具的三档与分组（tool 是对外名字）
- POST   /api/workspaces/{ws}/mcp-servers/probe                   登记前探一次地址（是不是 OAuth、能不能自动注册）

成员自己的连接在 /api/workspaces/{ws}/me/mcp-connections；OAuth 授权的回调是 GET /api/mcp/oauth/callback。
错误统一 {"detail": "..."}，文案面向用户。任何响应都不含请求头、令牌、client secret 明文。
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
from fastapi.responses import RedirectResponse

from ...audit.context import annotate
from ...auth.principal import Principal
from .admin import McpAdmin
from .enterprise import EnterpriseMcpHere
from .errors import McpError
from .me import McpMe
from .oauth_login import LoginFailed, McpOAuthLogin
from .schemas import (
    ConnectBody,
    ConnectionView,
    EnterpriseKeyBody,
    EnterpriseUsersBody,
    LoginBody,
    LoginView,
    McpServerCreate,
    McpServerPatch,
    McpServerView,
    McpToolPatch,
    ProbeBody,
    ProbeView,
)

log = logging.getLogger(__name__)
FALLBACK_PAGE = "/settings?tab=connections"

IdPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9_.\-]{1,64}$")]
ToolPath = Annotated[str, Path(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")]




async def _call(awaitable):
    try:
        return await awaitable
    except McpError as error:
        raise HTTPException(status_code=error.status, detail=str(error)) from error


def build_router(admin: McpAdmin, current_principal: Callable[..., Awaitable[Principal]]) -> APIRouter:
    router = APIRouter(prefix="/api/workspaces/{workspace_id}/mcp-servers", tags=["tools"])
    Who = Annotated[Principal, Depends(current_principal)]

    async def _view(awaitable) -> McpServerView:
        record = await _call(awaitable)
        return McpServerView.of(record, admin.redirect_uri, (await admin.promoted(record.workspace_id)).get(record.id))

    @router.get("", response_model=list[McpServerView], response_model_by_alias=True)
    async def list_servers(workspace_id: IdPath, who: Who):
        """列出 MCP 服务
        已经收为企业 MCP 的带 promotedTo（企业 MCP 的名称），确认企业 MCP 能用后可以删掉这份。"""
        records = await _call(admin.list_servers(who.user_id, workspace_id))
        promoted = await admin.promoted(workspace_id)
        return [McpServerView.of(record, admin.redirect_uri, promoted.get(record.id)) for record in records]

    @router.post("/probe", response_model=ProbeView, response_model_by_alias=True)
    async def probe(workspace_id: IdPath, body: ProbeBody, who: Who):
        """探测 MCP 服务的登录方式
        登记前探一次：要不要凭据（needsCredentials）、是不是 OAuth；不支持自动注册时要在对方那边建应用，回调地址填 redirectUri。"""
        found = await _call(admin.probe(who.user_id, workspace_id, body.url))
        return ProbeView(oauth=found.oauth, needs_credentials=found.needs_credentials,
                         dynamic_registration=found.dynamic_registration, scope=found.scope,
                         redirect_uri=found.redirect_uri)

    @router.post("", status_code=201, response_model=McpServerView, response_model_by_alias=True)
    async def create(workspace_id: IdPath, body: McpServerCreate, who: Who):
        """接入 MCP 服务"""
        return await _view(admin.create(who.user_id, workspace_id, body))

    @router.get("/{server_id}", response_model=McpServerView, response_model_by_alias=True)
    async def get(workspace_id: IdPath, server_id: IdPath, who: Who):
        """一个 MCP 服务的配置与工具"""
        return await _view(admin.get(who.user_id, workspace_id, server_id))

    @router.patch("/{server_id}", response_model=McpServerView, response_model_by_alias=True)
    async def update(workspace_id: IdPath, server_id: IdPath, body: McpServerPatch, who: Who):
        """修改 MCP 服务"""
        return await _view(admin.update(who.user_id, workspace_id, server_id, body))

    @router.delete("/{server_id}", status_code=204)
    async def delete(workspace_id: IdPath, server_id: IdPath, who: Who):
        """删除 MCP 服务"""
        await _call(admin.delete(who.user_id, workspace_id, server_id))
        return Response(status_code=204)

    @router.post("/{server_id}/sync", response_model=McpServerView, response_model_by_alias=True)
    async def sync(workspace_id: IdPath, server_id: IdPath, who: Who):
        """重新拉取 MCP 工具清单"""
        return await _view(admin.sync(who.user_id, workspace_id, server_id))

    @router.patch("/{server_id}/tools/{tool_name}", response_model=McpServerView, response_model_by_alias=True)
    async def configure_tool(workspace_id: IdPath, server_id: IdPath, tool_name: ToolPath, body: McpToolPatch,
                             who: Who):
        """修改单个 MCP 工具的风险等级与启停"""
        return await _view(admin.configure_tool(who.user_id, workspace_id, server_id, tool_name, body))

    return router


def build_me_router(me: McpMe, current_principal: Callable[..., Awaitable[Principal]]) -> APIRouter:
    """成员自己的连接：「设置 → 连接」。只认本人登录，不接受 API Key（凭据只能本人在页面上填）。"""
    router = APIRouter(prefix="/api/workspaces/{workspace_id}/me/mcp-connections", tags=["tools"])

    async def person(principal: Annotated[Principal, Depends(current_principal)]) -> Principal:
        if principal.is_api_key:
            raise HTTPException(status_code=403, detail="连接只能本人在页面上操作，不接受 API Key")
        return principal

    Who = Annotated[Principal, Depends(person)]

    @router.get("", response_model=list[ConnectionView], response_model_by_alias=True)
    async def list_connections(workspace_id: IdPath, who: Who):
        """我能用的 MCP 服务
        本人能用的服务与工具；要用自己凭据的带连接状态（已连接、已失效、没连接），其余是可以直接用。"""
        return await _call(me.list(who.user_id, workspace_id))

    @router.put("/{server_id}", response_model=ConnectionView, response_model_by_alias=True)
    async def connect(workspace_id: IdPath, server_id: IdPath, body: ConnectBody, who: Who):
        """连接 MCP 服务
        用本人的令牌连一次，对方接受才保存；令牌加密存，之后不再显示原文。"""
        annotate(resource_id=server_id)  # 不记令牌
        return await _call(me.connect(who.user_id, workspace_id, server_id, body.token))

    @router.post("/{server_id}/login", response_model=LoginView, response_model_by_alias=True)
    async def login(workspace_id: IdPath, server_id: IdPath, body: LoginBody, who: Who):
        """发起 MCP 服务的 OAuth 授权
        OAuth 授权的服务：返回对方的授权地址，浏览器跳过去登录，授权后回到 returnTo。"""
        annotate(resource_id=server_id)
        url = await _call(me.login(who.user_id, workspace_id, server_id, body.return_to))
        return LoginView(authorize_url=url)

    @router.delete("/{server_id}", status_code=204)
    async def disconnect(workspace_id: IdPath, server_id: IdPath, who: Who):
        """断开 MCP 服务"""
        await _call(me.disconnect(who.user_id, workspace_id, server_id))
        return Response(status_code=204)

    return router


def build_enterprise_here_router(here: EnterpriseMcpHere,
                                 current_principal: Callable[..., Awaitable[Principal]]) -> APIRouter:
    """空间管理员对到达本空间的企业 MCP：列出、停用或启用、改给谁用（不能改服务本身，那是系统管理员在扩展里管的）。"""
    router = APIRouter(prefix="/api/workspaces/{workspace_id}/enterprise-mcp", tags=["tools"])
    Who = Annotated[Principal, Depends(current_principal)]

    @router.get("")
    async def list_here(workspace_id: IdPath, who: Who):
        """本空间的企业 MCP
        系统管理员发到本空间的企业 MCP，以及本空间有没有停用、有没有收窄给谁用。"""
        return {"servers": [view.to_json() for view in await _call(here.list(who.user_id, workspace_id))]}

    for verb, off in (("disable", True), ("enable", False)):
        _here_toggle(router, here, verb, off, Who)

    @router.put("/users")
    async def set_users(workspace_id: IdPath, body: EnterpriseUsersBody, who: Who):
        """改企业 MCP 在本空间给谁用
        选了全体成员（role:editor）就是不收窄；否则只有选中的对象和管理员能用，一个都不选就只有管理员。"""
        annotate(resource_id=body.server_id, subjects=body.subjects)
        narrowed = await _call(here.set_users(who.user_id, workspace_id, body.server_id, body.subjects))
        return {"serverId": body.server_id, "narrowed": narrowed}

    return router


def _here_toggle(router: APIRouter, here: EnterpriseMcpHere, verb: str, off: bool, Who) -> None:
    @router.post(f"/{verb}", status_code=204)
    async def toggle(workspace_id: IdPath, body: EnterpriseKeyBody, who: Who):
        """在本空间停用或启用企业 MCP"""
        annotate(resource_id=body.server_id)
        await _call(here.set_off(who.user_id, workspace_id, body.server_id, off))
        return Response(status_code=204)


def build_oauth_router(login: McpOAuthLogin, current_principal: Callable[..., Awaitable[Principal]]) -> APIRouter:
    """OAuth 授权的回调：浏览器从对方授权页跳回来。成功或失败都跳回发起的页面（带 mcpConnected 或 mcpError），
    页面据此提示；要求是发起人本人的登录会话，别人拿到这个链接也用不了。"""
    router = APIRouter(prefix="/api/mcp/oauth", tags=["tools"])

    @router.get("/callback")
    async def callback(request: Request, state: str = "", code: str = "", iss: str | None = None,
                       error: str | None = None):
        """MCP 服务的 OAuth 授权回调"""
        annotate(record=True)  # 走 GET，也要进审计
        try:
            principal = await current_principal(request)
        except HTTPException:
            return _back(FALLBACK_PAGE, error="登录已过期，请先登录 KnowWork，再到「设置 → 连接」重新连接")
        if principal.is_api_key:
            return _back(FALLBACK_PAGE, error="连接只能本人在页面上操作")
        annotate(actor_id=principal.user_id)
        try:
            done = await login.complete(principal.user_id, state=state, code=code, iss=iss, error=error)
        except LoginFailed as failure:
            log.info("mcp oauth callback rejected: %s", failure)  # 不记 code、state、令牌
            return _back(failure.return_to or FALLBACK_PAGE, error=str(failure))
        except McpError as failure:  # 例如服务在登录途中被删了
            return _back(FALLBACK_PAGE, error=str(failure))
        annotate(resource_id=done.server_id)
        return _back(done.return_to, connected=done.server_id)

    return router


def _back(page: str, *, error: str | None = None, connected: str | None = None) -> RedirectResponse:
    params = {"mcpError": error} if error else {"mcpConnected": connected or ""}
    joiner = "&" if "?" in page else "?"
    return RedirectResponse(f"{page}{joiner}{urlencode(params)}", status_code=302)
