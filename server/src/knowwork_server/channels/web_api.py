"""网页嵌入的公开接口（channels/web.py），嵌入页与嵌入脚本用，不用登录：

- GET  /api/embed/v1/channels/{web_key}            公开配置（名字、开场白、快捷提问、外观、回答方式）；渠道停用或不存在 404
- POST /api/embed/v1/channels/{web_key}/sessions   换会话令牌（只放嵌入页内存，之后的对话接口都带它）：公开访问带访客 id，
                                                   认证访问带接入方后端签的 JWT（web_identity.py）
- POST /api/embed/v1/sessions/renew                用着就续：带着还没过期的会话令牌换一个新的
- GET  /api/embed/v1/frame-ancestors               给网关 auth_request 用（发布令牌由 key 或 X-Original-URI 给）：
                                                   允许嵌入的网站放在响应头 X-Frame-Ancestors

对话本身走普通的会话接口（/api/workspaces/{ws}/conversations/...），带会话令牌即可，见 identity/container.py 怎么认。
"""

import re
from dataclasses import asdict
from typing import Annotated
from urllib.parse import unquote

from fastapi import APIRouter, Header, HTTPException, Path, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ..conversations.api import Throttled
from ..net import client_ip
from .web import EmbedSession, WebChannelGone, WebChannels
from .web_identity import Rejected
from .web_tokens import PREFIX, VISITOR

KEY_PATTERN = r"^[A-Za-z0-9_.\-]{1,64}~[A-Za-z0-9_-]{16,64}$"
WebKey = Annotated[str, Path(pattern=KEY_PATTERN)]
GONE = "这个入口已经关闭或不存在"
FRAME_HEADER = "X-Frame-Ancestors"


class OpenBody(BaseModel):
    """公开访问带访客 id；认证访问带接入方后端签的 JWT。"""

    model_config = ConfigDict(extra="forbid")

    visitor: str | None = Field(default=None, pattern=VISITOR.pattern)
    token: str | None = Field(default=None, min_length=1, max_length=8192)


EMBED_PAGE = re.compile(r"^/embed/([^/?#]{1,200})")


def _embed_key(uri: str) -> str:
    """嵌入页路径 /embed/<发布令牌>?... 里的发布令牌；不是嵌入页为空。"""
    found = EMBED_PAGE.match(uri)
    return unquote(found.group(1)) if found else ""


def _session_view(session: EmbedSession) -> dict:
    return {"token": session.token, "expiresAt": session.expires_at, "workspaceId": session.workspace_id,
            "channelId": session.channel_id}


def build_web_router(web: WebChannels) -> APIRouter:
    router = APIRouter(prefix="/api/embed/v1", tags=["channels"])

    @router.get("/channels/{web_key}")
    async def config(web_key: WebKey):
        """网页嵌入的公开配置"""
        found = await web.config(web_key)
        if found is None:
            raise HTTPException(status_code=404, detail=GONE)
        view = asdict(found)
        # 嵌入脚本在接入方的页面里读它（给悬浮按钮配色、定位），跨站：放开来源。内容本来就是公开的，也不带 Cookie
        return JSONResponse({"title": view["title"], "greeting": view["greeting"], "starters": list(view["starters"]),
                             "color": view["color"], "position": view["position"], "answerMode": view["answer_mode"],
                             "access": view["access"]}, headers={"Access-Control-Allow-Origin": "*"})

    @router.post("/channels/{web_key}/sessions")
    async def open_session(web_key: WebKey, body: OpenBody, request: Request):
        """打开网页嵌入，换会话令牌

        公开访问带访客 id（嵌入页随机生成）；认证访问带接入方后端用认证密钥签的 JWT（HS256，sub、exp、jti 必填，
        有效期不超过 10 分钟，只能用一次），认不出或不让进时 403，说明直接给访客看。"""
        try:
            if body.token is not None:
                session = await web.open_identified(web_key, body.token, client_ip(request))
            elif body.visitor is not None:
                session = await web.open_public(web_key, body.visitor, client_ip(request))
            else:
                raise HTTPException(status_code=422, detail="要带访客 id 或登录信息")
        except Throttled as error:
            raise HTTPException(status_code=429, detail=str(error)) from error
        except WebChannelGone as error:
            raise HTTPException(status_code=404, detail=GONE) from error
        except Rejected as error:
            raise HTTPException(status_code=403, detail=str(error)) from error
        return _session_view(session)

    @router.post("/sessions/renew")
    async def renew(authorization: Annotated[str, Header()] = ""):
        """续期网页嵌入的会话令牌"""
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token.strip().startswith(PREFIX):
            raise HTTPException(status_code=401, detail="未登录或凭证无效")
        try:
            return _session_view(await web.renew(token.strip()))
        except WebChannelGone as error:
            raise HTTPException(status_code=401, detail="未登录或凭证无效") from error

    @router.get("/frame-ancestors", status_code=204)
    async def frame_ancestors(key: Annotated[str, Query(max_length=200)] = "",
                              x_original_uri: Annotated[str, Header(max_length=2000)] = ""):
        """允许嵌入这个网页渠道的网站（网关用）

        网关给嵌入页（/embed/<发布令牌>）加 `Content-Security-Policy: frame-ancestors <这个头>`；发布令牌由 key 给，
        或从网关带来的原始路径（X-Original-URI，auth_request 的子请求拿不到主请求路径里截出的变量）里取。
        渠道不在、停用或格式不对时只有 'self'：页面照样能整页打开，只是别的网站嵌不进去。"""
        found = key or _embed_key(x_original_uri)
        origins = await web.frame_ancestors(found) if found else ()
        return Response(status_code=204, headers={FRAME_HEADER: " ".join(("'self'", *origins))})

    return router
