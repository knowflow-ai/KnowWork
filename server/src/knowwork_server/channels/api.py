"""渠道的接口：

- GET    /api/workspaces/{ws}/channels                               渠道列表（空间所有者、管理员）
- POST   /api/workspaces/{ws}/channels                               新建
- PUT    /api/workspaces/{ws}/channels/{id}                          修改（Secret 不填就保留原来的）
- POST   /api/workspaces/{ws}/channels/{id}/enabled                  启用、停用
- POST   /api/workspaces/{ws}/channels/{id}/web-key                  重置网页渠道的发布令牌
- POST   /api/workspaces/{ws}/channels/{id}/web-secret               生成或重置网页渠道的认证密钥（明文只回这一次）
- DELETE /api/workspaces/{ws}/channels/{id}                          删除
- GET    /api/workspaces/{ws}/channels/{id}/sessions                 最近在问的人（私聊、群里）
- GET    /api/workspaces/{ws}/channels/{id}/strangers                认人的渠道里私聊了却没认出来的人
- GET    /api/workspaces/{ws}/channels/identity-sources?platform=    认人可选的组织来源（同平台）
- GET    /api/workspaces/{ws}/channels/answer-modes                  可选的回答方式（带群聊按哪类资源选）
- GET    /api/workspaces/{ws}/channels/targets                       网页渠道能挂的对象（扩展登记的，例如专家）
"""

from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Response
from pydantic import BaseModel, ConfigDict, Field

from ..auth.principal import Principal
from .admin import ChannelAdmin, ChannelInput, Forbidden, Invalid, NotFound
from .settings import ChannelSettings, WebOptions

IdPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9_.\-]{1,64}$")]
PrincipalDependency = Callable[..., Awaitable[Principal]]


class ChannelBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    # web 是网页渠道（嵌入代码与分享链接，channels/web.py）：没有机器人与 Secret，配置在 web
    platform: Literal["wecom", "feishu", "dingtalk", "web"] = "wecom"
    enabled: bool = True
    bot_id: str = Field(default="", max_length=128)  # IM 渠道必填（admin 里校验）
    bot_name: str = Field(default="", max_length=100)
    secret: str | None = Field(default=None, max_length=512)
    settings: ChannelSettings = Field(default_factory=ChannelSettings)
    # public 不认人（用渠道账号回答）；member 认人：私聊按提问人本人回答，settings.identity_source 选组织来源
    audience: Literal["public", "member"] = "public"
    web: WebOptions | None = None

    def input(self) -> ChannelInput:
        return ChannelInput(name=self.name, platform=self.platform, enabled=self.enabled, bot_id=self.bot_id,
                            bot_name=self.bot_name, secret=(self.secret or "").strip() or None,
                            settings=self.settings, audience=self.audience, web=self.web)


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

    @router.get(base + "/answer-modes")
    async def answer_modes(workspace_id: IdPath, who: Admin):
        """IM 渠道可选的回答方式"""
        return {"items": [asdict(view) for view in await _call(admin.answer_modes(who.user_id, workspace_id))]}

    @router.get(base + "/identity-sources")
    async def identity_sources(workspace_id: IdPath, who: Admin,
                               platform: Literal["wecom", "feishu", "dingtalk"] = "wecom"):
        """IM 渠道认人可选的组织来源"""
        found = await _call(admin.identity_sources(who.user_id, workspace_id, platform))
        return {"items": [asdict(view) for view in found]}

    @router.get(base + "/targets")
    async def targets(workspace_id: IdPath, who: Admin):
        """网页渠道能挂的对象（例如专家）"""
        return {"items": [asdict(view) for view in await _call(admin.targets(who.user_id, workspace_id))]}

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

    @router.post(base + "/{channel_id}/web-key")
    async def reset_web_key(workspace_id: IdPath, channel_id: IdPath, who: Admin):
        """重置网页渠道的发布令牌（旧的嵌入代码与分享链接一起失效）"""
        return asdict(await _call(admin.reset_web_key(who.user_id, workspace_id, channel_id)))

    @router.post(base + "/{channel_id}/web-secret")
    async def reset_web_secret(workspace_id: IdPath, channel_id: IdPath, who: Admin):
        """生成或重置网页渠道的认证密钥（明文只在这一次返回）"""
        view, plaintext = await _call(admin.reset_web_secret(who.user_id, workspace_id, channel_id))
        return {"channel": asdict(view), "secret": plaintext}

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

    @router.get(base + "/{channel_id}/strangers")
    async def strangers(workspace_id: IdPath, channel_id: IdPath, who: Admin):
        """IM 渠道里没认出来的人"""
        found = await _call(admin.strangers(who.user_id, workspace_id, channel_id))
        return {"items": [asdict(view) for view in found]}

    return router
