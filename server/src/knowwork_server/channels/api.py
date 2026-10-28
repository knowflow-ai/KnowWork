"""渠道的接口：

- GET    /api/workspaces/{ws}/channels                               渠道列表（空间所有者、管理员）
- POST   /api/workspaces/{ws}/channels                               新建
- PUT    /api/workspaces/{ws}/channels/{id}                          修改（Secret 不填就保留原来的）
- POST   /api/workspaces/{ws}/channels/{id}/enabled                  启用、停用
- DELETE /api/workspaces/{ws}/channels/{id}                          删除
- GET    /api/workspaces/{ws}/channels/{id}/sessions                 最近在问的人（私聊、群里）
"""

from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Response
from pydantic import BaseModel, ConfigDict, Field

from ..auth.principal import Principal
from .admin import ChannelAdmin, ChannelInput, Forbidden, Invalid, NotFound
from .settings import ChannelSettings

IdPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9_.\-]{1,64}$")]
PrincipalDependency = Callable[..., Awaitable[Principal]]


class ChannelBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    platform: Literal["wecom", "feishu", "dingtalk"] = "wecom"
    enabled: bool = True
    bot_id: str = Field(min_length=1, max_length=128)
    bot_name: str = Field(default="", max_length=100)
    secret: str | None = Field(default=None, max_length=512)
    settings: ChannelSettings = Field(default_factory=ChannelSettings)

    def input(self) -> ChannelInput:
        return ChannelInput(name=self.name, platform=self.platform, enabled=self.enabled, bot_id=self.bot_id,
                            bot_name=self.bot_name, secret=(self.secret or "").strip() or None,
                            settings=self.settings)


class EnabledBody(BaseModel):
    enabled: bool


async def _call(awaitable):
    try:
        return await awaitable
    except Forbidden as error:
        raise HTTPException(status_code=403, detail="只有空间所有者和管理员能管理渠道") from error
    except NotFound as error:
        raise HTTPException(status_code=404, detail="渠道不存在") from error
    except Invalid as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def build_router(admin: ChannelAdmin, workspace_principal: PrincipalDependency) -> APIRouter:
    router = APIRouter(tags=["channels"])
    Admin = Annotated[Principal, Depends(workspace_principal)]
    base = "/api/workspaces/{workspace_id}/channels"

    @router.get(base)
    async def list_channels(workspace_id: IdPath, who: Admin):
        """列出接入的 IM 渠道"""
        return {"items": [asdict(view) for view in await _call(admin.channels(who.user_id, workspace_id))]}

    @router.post(base, status_code=201)
    async def create(workspace_id: IdPath, body: ChannelBody, who: Admin):
        """接入 IM 渠道"""
        return asdict(await _call(admin.create(who.user_id, workspace_id, body.input())))

    @router.put(base + "/{channel_id}")
    async def update(workspace_id: IdPath, channel_id: IdPath, body: ChannelBody, who: Admin):
        """修改 IM 渠道"""
        return asdict(await _call(admin.update(who.user_id, workspace_id, channel_id, body.input())))

    @router.post(base + "/{channel_id}/enabled")
    async def set_enabled(workspace_id: IdPath, channel_id: IdPath, body: EnabledBody, who: Admin):
        """启用或停用 IM 渠道"""
        return asdict(await _call(admin.set_enabled(who.user_id, workspace_id, channel_id, body.enabled)))

    @router.delete(base + "/{channel_id}", status_code=204)
    async def delete(workspace_id: IdPath, channel_id: IdPath, who: Admin) -> Response:
        """删除 IM 渠道"""
        await _call(admin.delete(who.user_id, workspace_id, channel_id))
        return Response(status_code=204)

    @router.get(base + "/{channel_id}/sessions")
    async def sessions(workspace_id: IdPath, channel_id: IdPath, who: Admin):
        """IM 渠道里的会话"""
        found = await _call(admin.sessions(who.user_id, workspace_id, channel_id))
        return {"items": [asdict(view) for view in found]}

    return router
