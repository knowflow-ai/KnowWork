"""后台会话：扩展在 worker 里替某个用户发起 Agent 对话（例如评测时按某种回答方式逐题发问）。

对话只能在业务服务进程里跑（会话泵、宿主连接、令牌续期都在这里），worker 不能自己跑，所以经内部端口调这里的
四个接口：建会话、发一轮、看这一轮到哪了、取消。发消息和用户在页面上走同一条路（回答方式、@、补上下文、权限
都照常判），只是会话标成后台会话：不进本人的「最近」和搜索，打开链接照样能看，便于核对 Agent 做了什么。

令牌：worker 用部署主密钥按用途派生的子密钥签短期令牌（受众 background，带工作空间），这里核对受众、签名、
过期与空间。内部端口本来只给宿主和 worker 连，令牌是第二道。

- POST /internal/background/conversations                        建会话，body {user_id, title}
- POST /internal/background/conversations/{id}/messages          发一轮，body {user_id, text, answer_mode}
- GET  /internal/background/conversations/{id}?user_id=          最近一轮：status（running / completed / cancelled /
                                                                  failed，还没开始是 pending）、asking（在等用户回答）、answer
- POST /internal/background/conversations/{id}/cancel            取消正在跑的一轮，body {user_id}
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..access.rls import set_workspace
from ..db.base import in_workspace
from ..kernel.contract import ANSWER_MODE_PATTERN, TurnInput
from ..kernel.tokens import TokenError, TokenSigner, derive_key
from .answer_modes import AnswerModeUnavailable, UnknownAnswerMode
from .events_v1 import assistant_text
from .mention_kinds import MentionRejected
from .models import RunEvent, RunSummary
from .service import ConversationNotFound, ConversationService

AUDIENCE = "background"
PURPOSE = "background-turns"  # 子密钥的用途，和宿主回调的令牌互不通用
TOKEN_TTL_SECONDS = 60
PENDING = "pending"
ANSWER_EVENTS = 500  # 找回答时往回看的事件条数
ID = Annotated[str, Path(pattern=r"^[A-Za-z0-9_\-]{1,64}$")]
UserId = Annotated[str, Field(min_length=1, max_length=64)]
MemberCheck = Callable[[str, str], bool]  # (用户, 空间) → 是不是空间成员


def background_signer(master_key: str) -> TokenSigner:
    return TokenSigner(derive_key(master_key, PURPOSE))


class CreateBody(BaseModel):
    user_id: UserId
    title: str = Field(default="", max_length=200)


class SendBody(BaseModel):
    user_id: UserId
    text: str = Field(min_length=1, max_length=20_000)
    answer_mode: str = Field(default="agent", pattern=ANSWER_MODE_PATTERN)


class UserBody(BaseModel):
    user_id: UserId


@dataclass(frozen=True)
class TurnState:
    status: str  # pending | running | completed | cancelled | failed
    asking: bool = False  # Agent 在等用户回答（ask_user_question）
    answer: str = ""  # 最后一条助手消息的文字

    @property
    def ended(self) -> bool:
        return self.status not in (PENDING, "running")


def build_background_router(service: ConversationService, signer: TokenSigner, sessionmaker: async_sessionmaker, *,
                            member: MemberCheck) -> APIRouter:
    router = APIRouter(prefix="/internal/background/conversations")

    def workspace(authorization: Annotated[str, Header()] = "") -> str:
        scheme, _, token = authorization.partition(" ")
        try:
            if scheme.lower() != "bearer":
                raise TokenError("no bearer token")
            return signer.verify(token, audience=AUDIENCE).workspace_id
        except TokenError as error:
            raise HTTPException(status_code=401, detail="令牌无效") from error

    Workspace = Annotated[str, Depends(workspace)]

    def require_member(user_id: str, workspace_id: str) -> None:
        if not member(user_id, workspace_id):
            raise HTTPException(status_code=403, detail="这个人不是这个空间的成员")

    async def owned(conversation_id: str, workspace_id: str, user_id: str):
        require_member(user_id, workspace_id)
        try:
            return await service.get(conversation_id=conversation_id, workspace_id=workspace_id, user_id=user_id)
        except ConversationNotFound as error:
            raise HTTPException(status_code=404, detail="没有这个会话") from error

    # 会话这些表开了行级安全：公开接口在鉴权时声明当前空间，这里按令牌里的空间声明（同渠道机器人）
    @router.post("")
    async def create(body: CreateBody, workspace_id: Workspace) -> dict[str, str]:
        """建后台会话"""
        require_member(body.user_id, workspace_id)
        with in_workspace(workspace_id):
            conversation = await service.create(workspace_id=workspace_id, user_id=body.user_id, title=body.title,
                                                background=True)
        return {"id": conversation.id}

    @router.post("/{conversation_id}/messages")
    async def send(conversation_id: ID, body: SendBody, workspace_id: Workspace) -> dict[str, str]:
        """在后台会话里发一轮"""
        with in_workspace(workspace_id):
            conversation = await owned(conversation_id, workspace_id, body.user_id)
            try:
                message_id = await service.send(conversation, TurnInput(text=body.text, answer_mode=body.answer_mode))
            except (UnknownAnswerMode, MentionRejected) as error:
                raise HTTPException(status_code=422, detail="不认识这个回答方式，或这个人现在不能用") from error
            except AnswerModeUnavailable as error:
                raise HTTPException(status_code=503, detail="这个回答方式暂时不可用") from error
        return {"message_id": message_id}

    @router.get("/{conversation_id}")
    async def state(conversation_id: ID, workspace_id: Workspace,
                    user_id: Annotated[str, Query(min_length=1, max_length=64)]) -> dict[str, object]:
        """后台会话最近一轮到哪了"""
        with in_workspace(workspace_id):
            conversation = await owned(conversation_id, workspace_id, user_id)
            status, answer = await _latest(sessionmaker, workspace_id, conversation_id)
            asking = bool(await service.questions(conversation)) if status == "running" else False
        return {"status": status, "asking": asking, "answer": answer}

    @router.post("/{conversation_id}/cancel")
    async def cancel(conversation_id: ID, body: UserBody, workspace_id: Workspace) -> dict[str, bool]:
        """取消后台会话正在跑的一轮"""
        with in_workspace(workspace_id):
            conversation = await owned(conversation_id, workspace_id, body.user_id)
            await service.cancel(conversation)
        return {"ok": True}

    return router


async def _latest(sessionmaker: async_sessionmaker, workspace_id: str, conversation_id: str) -> tuple[str, str]:
    """最近一轮的状态（运行记录）与最后一条助手消息（事件是批量异步落库的，刚结束时可能还差最后几条）。"""
    async with sessionmaker() as session, session.begin():
        await set_workspace(session, workspace_id)
        summary = await session.scalar(select(RunSummary).where(RunSummary.conversation_id == conversation_id)
                                       .order_by(RunSummary.turn.desc()).limit(1))
        events = await session.scalars(select(RunEvent).where(RunEvent.conversation_id == conversation_id,
                                                              RunEvent.kind == "session/event")
                                       .order_by(RunEvent.seq.desc()).limit(ANSWER_EVENTS))
        answer = next((text for text in (_assistant(e.data) for e in events) if text), "")
    if summary is None:
        return PENDING, answer
    return (summary.status if summary.ended_at is not None else "running"), answer


def _assistant(data: object) -> str:
    if not isinstance(data, dict) or data.get("type") != "assistant/message":
        return ""
    return assistant_text(data.get("data"))


class BackgroundTurnError(Exception):
    """业务服务拒绝或连不上；message 是给扩展记日志、写进任务原因的一句。status 是业务服务回的 HTTP 状态（连不上时
    为空）：4xx 是这个人、这个回答方式本身不行，重试没用；5xx 和连不上可以重试。"""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status

    @property
    def retryable(self) -> bool:
        return self.status is None or self.status >= 500


class BackgroundTurns:
    """worker 里的客户端：扩展经 WorkerContext.turns 用它替用户发起对话。每次请求现签一张短期令牌。"""

    def __init__(self, base_url: str, signer: TokenSigner, http: httpx.AsyncClient) -> None:
        self._base = f"{base_url.rstrip('/')}/internal/background/conversations"
        self._signer, self._http = signer, http

    async def create(self, workspace_id: str, user_id: str, *, title: str = "") -> str:
        body = await self._call("POST", "", workspace_id, json={"user_id": user_id, "title": title})
        return str(body["id"])

    async def send(self, workspace_id: str, user_id: str, conversation_id: str, text: str, *,
                   answer_mode: str = "agent") -> None:
        await self._call("POST", f"/{conversation_id}/messages", workspace_id,
                         json={"user_id": user_id, "text": text, "answer_mode": answer_mode})

    async def state(self, workspace_id: str, user_id: str, conversation_id: str) -> TurnState:
        body = await self._call("GET", f"/{conversation_id}", workspace_id, params={"user_id": user_id})
        return TurnState(status=str(body["status"]), asking=bool(body.get("asking")), answer=str(body.get("answer", "")))

    async def cancel(self, workspace_id: str, user_id: str, conversation_id: str) -> None:
        await self._call("POST", f"/{conversation_id}/cancel", workspace_id, json={"user_id": user_id})

    async def _call(self, method: str, path: str, workspace_id: str, **kwargs) -> dict:
        token = self._signer.issue(audience=AUDIENCE, workspace_id=workspace_id, ttl_seconds=TOKEN_TTL_SECONDS)
        try:
            response = await self._http.request(method, f"{self._base}{path}",
                                                headers={"Authorization": f"Bearer {token}"}, **kwargs)
        except httpx.HTTPError as error:
            raise BackgroundTurnError(f"连不上业务服务的内部端口：{type(error).__name__}") from error
        if response.status_code >= 400:
            detail = _detail(response)
            raise BackgroundTurnError(f"业务服务拒绝了（{response.status_code}）：{detail}", status=response.status_code)
        return response.json()


def _detail(response: httpx.Response) -> str:
    try:
        return str(response.json().get("detail", ""))[:200]
    except ValueError:
        return response.text[:200]
