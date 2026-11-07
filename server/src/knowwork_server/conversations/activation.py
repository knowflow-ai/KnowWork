"""宿主请业务服务激活对话：宿主上的插件要在某段对话里自己发起一轮（定时任务到点）之前先来这里。

KnowWork 的每一轮都要业务服务认可：这个人此刻还是空间成员、对话还在且归他，用他此刻的配置与新签的令牌打开宿主会话、
开起事件泵，宿主随后投进去的消息和回答才记得进对话。宿主说是哪类插件、哪段对话、对话归谁（宿主上可能已经没有这段
对话，属主是它建任务时记下的）；能不能激活由这里按自己的数据判，宿主给的属主只用来按空间查对话（行级安全）。

认证用宿主令牌（业务服务调宿主用的同一个）：内部端口本来只给宿主和 worker 连，令牌是第二道。

- POST /internal/host/v1/activations   body {kind, sessionId, owner: {workspaceId, userId}}
"""

import hmac
import logging
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ..db.base import in_workspace
from ..kernel.contract import KernelError
from .service import ConversationNotFound, ConversationService

log = logging.getLogger(__name__)

# 允许自己发起一轮的宿主插件。新接一类（以后的事件触发等）要在这里登记，业务服务明确同意才放行
KINDS = frozenset({"schedule"})
MemberCheck = Callable[[str, str], bool]  # (用户, 空间) → 是不是空间成员


class Owner(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    workspace_id: str = Field(alias="workspaceId", min_length=1, max_length=128)
    user_id: str = Field(alias="userId", min_length=1, max_length=128)


class ActivationBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    kind: str = Field(min_length=1, max_length=64)
    session_id: str = Field(alias="sessionId", pattern=r"^[A-Za-z0-9_\-]{1,64}$")
    owner: Owner


def build_activation_router(service: ConversationService, host_token: str, *, member: MemberCheck) -> APIRouter:
    router = APIRouter(prefix="/internal/host/v1")
    expected = host_token.encode("utf-8")

    def require_host(authorization: Annotated[str, Header()] = "") -> None:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(token.encode("utf-8"), expected):
            raise HTTPException(status_code=401, detail="宿主令牌无效")

    @router.post("/activations", dependencies=[Depends(require_host)])
    async def activate(body: ActivationBody) -> dict[str, bool]:
        """激活对话，让宿主插件在里面发起一轮"""
        if body.kind not in KINDS:
            raise HTTPException(status_code=422, detail=f"不接受这类插件自己发起：{body.kind}")
        workspace_id, user_id = body.owner.workspace_id, body.owner.user_id
        if not member(user_id, workspace_id):
            raise HTTPException(status_code=403, detail="这个人不是这个空间的成员")
        with in_workspace(workspace_id):
            try:
                conversation = await service.get(conversation_id=body.session_id, workspace_id=workspace_id,
                                                 user_id=user_id)
            except ConversationNotFound as error:
                raise HTTPException(status_code=404, detail="没有这个对话，或它不归这个人") from error
            try:
                await service.activate(conversation)
            except KernelError as error:
                raise HTTPException(status_code=503, detail=f"宿主没能打开这段对话：{error.code}") from error
        log.info("activated conversation %s for host plugin %s", conversation.id, body.kind)
        return {"ok": True}

    return router
