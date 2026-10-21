"""MCP 服务的管理接口（公开端口，管理后台「工具与 MCP」页用）。权限判定在 McpAdmin 里。

- GET    /api/workspaces/{ws}/mcp-servers                         列表（含工具清单快照）
- POST   /api/workspaces/{ws}/mcp-servers                         登记：先连一次、拉工具清单，连不上不保存
- GET    /api/workspaces/{ws}/mcp-servers/{id}
- PATCH  /api/workspaces/{ws}/mcp-servers/{id}                    改名称、地址、请求头、超时、启停
- DELETE /api/workspaces/{ws}/mcp-servers/{id}
- POST   /api/workspaces/{ws}/mcp-servers/{id}/sync               重新拉工具清单
- PATCH  /api/workspaces/{ws}/mcp-servers/{id}/tools/{tool}       改单个工具的风险等级、审批方式、启停（tool 是对外名字）

错误统一 {"detail": "..."}，文案面向用户。任何响应都不含请求头明文。
"""

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Response

from ...auth.principal import Principal
from .admin import McpAdmin
from .errors import McpError
from .schemas import McpServerCreate, McpServerPatch, McpServerView, McpToolPatch

IdPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9_.\-]{1,64}$")]
ToolPath = Annotated[str, Path(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")]


async def _view(awaitable) -> McpServerView:
    return McpServerView.of(await _call(awaitable))


async def _call(awaitable):
    try:
        return await awaitable
    except McpError as error:
        raise HTTPException(status_code=error.status, detail=str(error)) from error


def build_router(admin: McpAdmin, current_principal: Callable[..., Awaitable[Principal]]) -> APIRouter:
    router = APIRouter(prefix="/api/workspaces/{workspace_id}/mcp-servers", tags=["tools"])
    Who = Annotated[Principal, Depends(current_principal)]

    @router.get("", response_model=list[McpServerView], response_model_by_alias=True)
    async def list_servers(workspace_id: IdPath, who: Who):
        return [McpServerView.of(record) for record in await _call(admin.list_servers(who.user_id, workspace_id))]

    @router.post("", status_code=201, response_model=McpServerView, response_model_by_alias=True)
    async def create(workspace_id: IdPath, body: McpServerCreate, who: Who):
        return await _view(admin.create(who.user_id, workspace_id, body))

    @router.get("/{server_id}", response_model=McpServerView, response_model_by_alias=True)
    async def get(workspace_id: IdPath, server_id: IdPath, who: Who):
        return await _view(admin.get(who.user_id, workspace_id, server_id))

    @router.patch("/{server_id}", response_model=McpServerView, response_model_by_alias=True)
    async def update(workspace_id: IdPath, server_id: IdPath, body: McpServerPatch, who: Who):
        return await _view(admin.update(who.user_id, workspace_id, server_id, body))

    @router.delete("/{server_id}", status_code=204)
    async def delete(workspace_id: IdPath, server_id: IdPath, who: Who):
        await _call(admin.delete(who.user_id, workspace_id, server_id))
        return Response(status_code=204)

    @router.post("/{server_id}/sync", response_model=McpServerView, response_model_by_alias=True)
    async def sync(workspace_id: IdPath, server_id: IdPath, who: Who):
        return await _view(admin.sync(who.user_id, workspace_id, server_id))

    @router.patch("/{server_id}/tools/{tool_name}", response_model=McpServerView, response_model_by_alias=True)
    async def configure_tool(workspace_id: IdPath, server_id: IdPath, tool_name: ToolPath, body: McpToolPatch,
                             who: Who):
        return await _view(admin.configure_tool(who.user_id, workspace_id, server_id, tool_name, body))

    return router
