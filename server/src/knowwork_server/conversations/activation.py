"""宿主请业务服务激活对话：宿主上的插件要在某段对话里自己发起一轮（定时任务到点、长任务目标接着做下一轮）之前先来这里。

KnowWork 的每一轮都要业务服务认可：这个人此刻还是空间成员、对话还在且归他，用他此刻的配置与新签的令牌打开宿主会话、
开起事件泵，宿主随后投进去的消息和回答才记得进对话。宿主说是哪类插件、哪段对话、对话归谁（宿主上可能已经没有这段
对话，属主是它建任务时记下的）；能不能激活由这里按自己的数据判，宿主给的属主只用来按空间查对话（行级安全）。

认证用宿主令牌（业务服务调宿主用的同一个）：内部端口本来只给宿主和 worker 连，令牌是第二道。
个人专属宿主拿不到这把共享的令牌（它里面跑着没审过的上传插件，拿到就能激活任何人的对话），用的是按这个人签的激活令牌
（受众 ACTIVATION_AUDIENCE，带归属，和模型令牌一起下发、续期，见 hosts/assembly.py）：只能激活归属是他本人的对话。

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
from .quota import QuotaExceeded
from .service import ConversationNotFound, ConversationService

log = logging.getLogger(__name__)

ACTIVATION_AUDIENCE = "host-activation"
# 认按人签的激活令牌：认出来返回它的归属（空间，人），认不出返回 None
OwnerOf = Callable[[str], tuple[str, str] | None]

# 允许自己发起一轮的宿主插件。新接一类（以后的事件触发等）要在这里登记，业务服务明确同意才放行：
# schedule 定时任务到点；goal 长任务目标自动接着做下一轮（官方 dsh-goal-round-driver，宿主的 goal-gate.mjs 每轮来问）
KINDS = frozenset({"schedule", "goal"})
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


def build_activation_router(service: ConversationService, host_token: str, *, member: MemberCheck,
                            owner_of: OwnerOf | None = None) -> APIRouter:
    """owner_of 认个人专属宿主的激活令牌；不给时只认共享宿主的令牌。"""
    router = APIRouter(prefix="/internal/host/v1")
    expected = host_token.encode("utf-8")

    def require_host(authorization: Annotated[str, Header()] = "") -> tuple[str, str] | None:
        """共享宿主返回 None（谁的对话都能激活）；专属宿主返回它归谁（只能激活这个人的）。"""
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() == "bearer" and hmac.compare_digest(token.encode("utf-8"), expected):
            return None
        owner = owner_of(token) if scheme.lower() == "bearer" and owner_of is not None else None
        if owner is None:
            raise HTTPException(status_code=401, detail="宿主令牌无效")
        return owner

    @router.post("/activations")
    async def activate(body: ActivationBody,
                       caller: Annotated[tuple[str, str] | None, Depends(require_host)]) -> dict[str, bool]:
        """激活对话，让宿主插件在里面发起一轮"""
        if body.kind not in KINDS:
            raise HTTPException(status_code=422, detail=f"不接受这类插件自己发起：{body.kind}")
        workspace_id, user_id = body.owner.workspace_id, body.owner.user_id
        if caller is not None and caller != (workspace_id, user_id):
            raise HTTPException(status_code=403, detail="这台宿主只能激活它主人自己的对话")
        if not member(user_id, workspace_id):
            raise HTTPException(status_code=403, detail="这个人不是这个空间的成员")
        with in_workspace(workspace_id):
            try:
                conversation = await service.get(conversation_id=body.session_id, workspace_id=workspace_id,
                                                 user_id=user_id)
            except ConversationNotFound as error:
                raise HTTPException(status_code=404, detail="没有这个对话，或它不归这个人") from error
            try:
                await service.activate(conversation, trigger="goal" if body.kind == "goal" else "schedule")
            except QuotaExceeded as error:
                # 额度用完：这次到点不执行（宿主记日志），下次到点再判
                log.info("not activating conversation %s for host plugin %s: quota exceeded (%s)",
                         conversation.id, body.kind, error.scope)
                raise HTTPException(status_code=429, detail=error.detail()) from error
            except KernelError as error:
                raise HTTPException(status_code=503, detail=f"宿主没能打开这段对话：{error.code}") from error
        log.info("activated conversation %s for host plugin %s", conversation.id, body.kind)
        return {"ok": True}

    return router
