"""上传的插件的接口。

空间所有者和管理员（空间插件与管控）：
- GET    /api/workspaces/{ws}/plugins/uploads          空间插件、成员安装的个人插件、管控开关
- POST   /api/workspaces/{ws}/plugins/uploads          上传一个空间插件（请求体是 .tgz）
- PUT    /api/workspaces/{ws}/plugins/uploads/{id}     改档、适用于、给谁用
- DELETE /api/workspaces/{ws}/plugins/uploads/{id}     删除
- POST   /api/workspaces/{ws}/plugins/uploads/{id}/adopt  把成员的个人插件转成空间插件
- GET    /api/workspaces/{ws}/plugins/uploads/{id}/detail 说明文档（插件详情里看）
- PUT    /api/workspaces/{ws}/plugins/settings         管控开关：允许成员安装个人插件、禁用清单

成员本人（个人插件，和给了我的空间插件的开关）：
- GET    /api/workspaces/{ws}/me/plugins/uploads               给了我的空间插件和我自己装的
- POST   /api/workspaces/{ws}/me/plugins/uploads               上传一个个人插件
- PUT    /api/workspaces/{ws}/me/plugins/uploads/{id}          开或关
- POST   /api/workspaces/{ws}/me/plugins/uploads/{id}/submit   把我装的提交给管理员
- GET    /api/workspaces/{ws}/me/plugins/uploads/{id}/detail   说明文档（插件详情里看）
- DELETE /api/workspaces/{ws}/me/plugins/uploads/{id}          删除我自己装的
- GET    /api/workspaces/{ws}/me/plugins/uploads/{id}/archive  下载压缩包（桌面端同步用）
- GET    /api/workspaces/{ws}/me/plugins/device                我的本机对话要装哪些（桌面端同步用）

这两组路由要排在预置插件的路由前面（那边有 /{key}，会吃掉 settings、uploads 这些固定路径）。
"""

from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from ..auth.principal import Principal
from .archive import MAX_ARCHIVE_BYTES
from .models import TARGET_DEVICE
from .service import Forbidden, Invalid
from .uploads import SCOPE_PERSONAL, SCOPE_WORKSPACE, NotFound, PluginSettings, UploadService

IdPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9_.\-]{1,64}$")]
PluginId = Annotated[str, Path(pattern=r"^pi_[0-9a-f]{32}$")]
Targets = Annotated[str | None, Query(pattern=r"^(cloud|device)(,(cloud|device))?$",
                                      description="适用于：cloud 云端对话、device 本机对话，逗号分隔；不给是两个都选")]
ARCHIVE_TYPES = frozenset({"application/gzip", "application/x-gzip", "application/x-tgz", "application/x-tar",
                           "application/octet-stream"})
Target = Literal["cloud", "device"]


class UpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["off", "on", "optional"] | None = None
    targets: list[Target] | None = Field(default=None, max_length=2)
    # 给谁用：role:editor 是全体成员；否则是部门、用户组、个人这些授权对象
    subjects: list[Annotated[str, Field(max_length=128)]] | None = Field(default=None, max_length=200)


class SettingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow_personal: bool
    allow_personal_device: bool
    blocked: list[Annotated[str, Field(max_length=128)]] = Field(default_factory=list, max_length=200)


class SwitchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


async def _read_archive(request: Request) -> bytes:
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type not in ARCHIVE_TYPES:
        raise HTTPException(status_code=415, detail="请上传 .tgz 压缩包")
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_ARCHIVE_BYTES:
        raise HTTPException(status_code=413, detail="压缩包超过大小上限")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_ARCHIVE_BYTES:
            raise HTTPException(status_code=413, detail="压缩包超过大小上限")
        chunks.append(chunk)
    return b"".join(chunks)


async def _call(awaitable, denied: str):
    try:
        return await awaitable
    except Forbidden as error:
        raise HTTPException(status_code=403, detail=denied) from error
    except NotFound as error:
        raise HTTPException(status_code=404, detail="没有这个插件") from error
    except Invalid as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def _split(targets: str | None) -> list[str] | None:
    return targets.split(",") if targets else None


def build_uploads_router(service: UploadService, current_principal: Callable[..., Awaitable[Principal]]) -> APIRouter:
    router = APIRouter(prefix="/api/workspaces/{workspace_id}/plugins", tags=["plugins"])
    Who = Annotated[Principal, Depends(current_principal)]
    denied = "只有空间所有者和管理员能管理插件"

    @router.get("/uploads")
    async def workspace_uploads(workspace_id: IdPath, who: Who) -> dict:
        """空间上传的插件
        空间插件（带档、适用于、给谁用）、成员安装的个人插件，和管控开关。"""
        shared, personal = await _call(service.workspace_list(who.user_id, workspace_id), denied)
        return {"plugins": [asdict(item) for item in shared], "member_plugins": [asdict(item) for item in personal],
                "settings": asdict(await service.settings(workspace_id)), "cloud_available": service.cloud}

    @router.post("/uploads", status_code=201)
    async def upload_workspace_plugin(workspace_id: IdPath, request: Request, who: Who,
                                      targets: Targets = None) -> dict:
        """上传空间插件
        请求体是 .tgz（DSH 的插件包，依赖打在包里）。只做静态检查，不执行包里的代码；同名再传是升级。
        传完默认是关闭，定了档才生效。"""
        data = await _read_archive(request)
        return asdict(await _call(service.install(who.user_id, workspace_id, SCOPE_WORKSPACE, data, _split(targets)),
                                  denied))

    @router.put("/uploads/{plugin_id}")
    async def update_workspace_plugin(workspace_id: IdPath, plugin_id: PluginId, body: UpdateBody, who: Who) -> dict:
        """改空间插件的档、适用于、给谁用
        没给的项不动。给谁用里有全体成员（role:editor）就是全空间，否则只给选中的对象。"""
        return asdict(await _call(service.update(who.user_id, workspace_id, plugin_id, mode=body.mode,
                                                 targets=body.targets, subjects=body.subjects), denied))

    @router.delete("/uploads/{plugin_id}", status_code=204)
    async def delete_workspace_plugin(workspace_id: IdPath, plugin_id: PluginId, who: Who) -> Response:
        """删除空间插件"""
        await _call(service.remove(who.user_id, workspace_id, plugin_id), denied)
        return Response(status_code=204)

    @router.post("/uploads/{plugin_id}/adopt", status_code=201)
    async def adopt_member_plugin(workspace_id: IdPath, plugin_id: PluginId, who: Who) -> dict:
        """把成员的个人插件转成空间插件
        用同一份压缩包建一个空间插件，转完默认关闭，再定档和给谁用。"""
        return asdict(await _call(service.adopt(who.user_id, workspace_id, plugin_id), denied))

    @router.get("/uploads/{plugin_id}/detail")
    async def workspace_plugin_detail(workspace_id: IdPath, plugin_id: PluginId, who: Who) -> dict:
        """上传插件的说明文档
        插件包里作者写的说明（语言文件指定的 Markdown，没有就是 README），上传时读出来存下的。"""
        return {"detail": await _call(service.detail(who.user_id, workspace_id, plugin_id, as_admin=True), denied)}

    @router.put("/settings")
    async def set_plugin_settings(workspace_id: IdPath, body: SettingsBody, who: Who) -> dict:
        """改插件的管控开关
        允许成员安装个人插件、允许给本机对话装个人插件、禁用清单（按包名，成员装了也不生效）。"""
        settings = PluginSettings(allow_personal=body.allow_personal,
                                  allow_personal_device=body.allow_personal_device, blocked=tuple(body.blocked))
        return asdict(await _call(service.set_settings(who.user_id, workspace_id, settings), denied))

    return router


def build_my_uploads_router(service: UploadService,
                            current_principal: Callable[..., Awaitable[Principal]]) -> APIRouter:
    router = APIRouter(prefix="/api/workspaces/{workspace_id}/me/plugins", tags=["plugins"])
    Who = Annotated[Principal, Depends(current_principal)]
    denied = "你不是这个空间的成员"

    @router.get("/uploads")
    async def my_uploads(workspace_id: IdPath, who: Who) -> dict:
        """我的上传插件
        管理员给了我的空间插件，和我自己装的个人插件；带这个空间允不允许我自己装。"""
        plugins = await _call(service.mine(who.user_id, workspace_id), denied)
        settings = await service.settings(workspace_id)
        return {"plugins": [asdict(item) for item in plugins], "can_install": settings.allow_personal,
                "can_install_device": settings.allow_personal and settings.allow_personal_device,
                "cloud_available": service.cloud}

    @router.post("/uploads", status_code=201)
    async def upload_my_plugin(workspace_id: IdPath, request: Request, who: Who, targets: Targets = None) -> dict:
        """上传个人插件
        请求体是 .tgz。只有我自己用，装完默认开着；同名再传是升级。空间管理员可以关掉这个入口。"""
        data = await _read_archive(request)
        return asdict(await _call(service.install(who.user_id, workspace_id, SCOPE_PERSONAL, data, _split(targets)),
                                  denied))

    @router.put("/uploads/{plugin_id}")
    async def switch_my_upload(workspace_id: IdPath, plugin_id: PluginId, body: SwitchBody, who: Who) -> dict:
        """开或关一个上传的插件
        管理员设为默认启用的不能关。新开的对话生效。"""
        return asdict(await _call(service.switch(who.user_id, workspace_id, plugin_id, body.enabled), denied))

    @router.get("/uploads/{plugin_id}/detail")
    async def my_plugin_detail(workspace_id: IdPath, plugin_id: PluginId, who: Who) -> dict:
        """我的上传插件的说明文档
        只能看「我的插件」里有的（给了我的空间插件、我自己装的）。"""
        return {"detail": await _call(service.detail(who.user_id, workspace_id, plugin_id, as_admin=False), denied)}

    @router.post("/uploads/{plugin_id}/submit")
    async def submit_my_plugin(workspace_id: IdPath, plugin_id: PluginId, who: Who) -> dict:
        """把我装的插件提交给管理员
        管理员看到后可以把它转成空间插件、授权给别人。"""
        return asdict(await _call(service.submit(who.user_id, workspace_id, plugin_id), denied))

    @router.delete("/uploads/{plugin_id}", status_code=204)
    async def delete_my_plugin(workspace_id: IdPath, plugin_id: PluginId, who: Who) -> Response:
        """删除我装的个人插件"""
        await _call(service.remove_own(who.user_id, workspace_id, plugin_id), denied)
        return Response(status_code=204)

    @router.get("/uploads/{plugin_id}/archive")
    async def download_plugin_archive(workspace_id: IdPath, plugin_id: PluginId, who: Who) -> Response:
        """下载插件压缩包
        只有对我生效的插件能下；桌面端同步本机对话的插件时用，响应头带摘要供核对。"""
        installed, digest, data = await _call(service.archive(who.user_id, workspace_id, plugin_id), denied)
        return Response(content=data, media_type="application/gzip",
                        headers={"x-plugin-digest": digest,
                                 "x-plugin-version": installed.version, "cache-control": "private, no-store"})

    @router.get("/device")
    async def my_device_plugins(workspace_id: IdPath, who: Who) -> dict:
        """我的本机对话要装的插件
        桌面端登录后取这份清单：按摘要下载缺的、卸掉不在清单里的。"""
        plugins = await _call(service.effective_for(who.user_id, workspace_id, TARGET_DEVICE), denied)
        return {"plugins": [asdict(item) for item in plugins]}

    return router
