"""插件的接口。

空间所有者和管理员：
- GET /api/workspaces/{ws}/plugins            这个空间的插件与各自的档
- PUT /api/workspaces/{ws}/plugins/{key}      定一个插件的档（off 关闭、on 全员启用、optional 可选）

成员本人：
- GET /api/workspaces/{ws}/me/plugins         管理员给了我的插件，和我自己的开关
- PUT /api/workspaces/{ws}/me/plugins/{key}   开或关一个「可选」的插件

改完以后新开的对话和下一次恢复的对话生效；正在进行的一轮不受影响。
"""

from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, ConfigDict

from ..auth.principal import Principal
from .service import Forbidden, Invalid, PluginService

IdPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9_.\-]{1,64}$")]
KeyPath = Annotated[str, Path(pattern=r"^[a-z][a-z0-9-]{0,47}$")]


class ModeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["off", "on", "optional"]


class SwitchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


def build_router(service: PluginService, current_principal: Callable[..., Awaitable[Principal]]) -> APIRouter:
    router = APIRouter(prefix="/api/workspaces/{workspace_id}/plugins", tags=["plugins"])
    Who = Annotated[Principal, Depends(current_principal)]
    denied = "只有空间所有者和管理员能管理插件"

    @router.get("")
    async def workspace_plugins(workspace_id: IdPath, who: Who) -> dict:
        """空间的插件
        这个空间能用的插件，和管理员给每个定的档：关闭、全员启用、可选（成员自己决定开不开）。"""
        try:
            return {"plugins": [asdict(item) for item in await service.workspace_plugins(who.user_id, workspace_id)]}
        except Forbidden as error:
            raise HTTPException(status_code=403, detail=denied) from error

    @router.put("/{key}")
    async def set_mode(workspace_id: IdPath, key: KeyPath, body: ModeBody, who: Who) -> dict:
        """定一个插件的档
        新开的对话和下一次恢复的对话按新的档生效。"""
        try:
            return asdict(await service.set_mode(who.user_id, workspace_id, key, body.mode))
        except Forbidden as error:
            raise HTTPException(status_code=403, detail=denied) from error
        except Invalid as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    return router


def build_me_router(service: PluginService, current_principal: Callable[..., Awaitable[Principal]]) -> APIRouter:
    router = APIRouter(prefix="/api/workspaces/{workspace_id}/me/plugins", tags=["plugins"])
    Who = Annotated[Principal, Depends(current_principal)]
    denied = "你不是这个空间的成员"

    @router.get("")
    async def my_plugins(workspace_id: IdPath, who: Who) -> dict:
        """我的插件
        管理员给了我的插件：全员启用的不能关，可选的带我自己的开关。"""
        try:
            return {"plugins": [asdict(item) for item in await service.my_plugins(who.user_id, workspace_id)]}
        except Forbidden as error:
            raise HTTPException(status_code=403, detail=denied) from error

    @router.put("/{key}")
    async def switch(workspace_id: IdPath, key: KeyPath, body: SwitchBody, who: Who) -> dict:
        """开或关我的一个插件
        只能开关管理员定为「可选」的；新开的对话和下一次恢复的对话生效。"""
        try:
            return asdict(await service.switch(who.user_id, workspace_id, key, body.enabled))
        except Forbidden as error:
            raise HTTPException(status_code=403, detail=denied) from error
        except Invalid as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    return router
