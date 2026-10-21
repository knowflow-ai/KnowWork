"""会话的公开接口。调用者由装配方给的依赖算出（账号会话或 API Key，加空间成员判定），本模块不认身份。

错误映射统一：会话不属于当前用户或不存在 → 404；宿主侧会话不存在 → 404；宿主忙 → 409；
审批与提问不存在或已结束 → 404，回答对不上问题 → 400，回答已在排队 → 409；
宿主或网络的其他错误 → 502；工具令牌签不出来 → 503，成员身份已被收回 → 403。
对外只给固定文案，原始错误只写服务端日志。
"""

import hmac
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Response
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from ..audit.context import annotate
from ..kernel.contract import (
    KernelError,
    QuestionAnswer,
    QuestionAnswerItem,
    SessionBusy,
    SessionNotFound,
    TurnInput,
)
from ..profile.errors import ProfileUnavailable
from ..profile.snapshot import MAX_SELECTED, TurnSelection
from .models import Conversation
from .replay import parse_cursor
from .service import MAX_PAGE, AttachmentUnavailable, ConversationNotFound, ConversationService
from .tool_credentials import ToolAccessRevoked, ToolCredentialUnavailable

log = logging.getLogger(__name__)

APPROVAL_ID = Path(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
# 提问以工具调用 id 为身份，各家模型的调用 id 都在这个字符集里
QUESTION_ID = Path(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")


class Actor(BaseModel):
    """会话接口眼里的调用者：谁、在哪个工作空间。"""

    user_id: str
    workspace_id: str


ActorDependency = Callable[..., Awaitable[Actor]]


class CreateConversation(BaseModel):
    title: str = Field(default="", max_length=200)


class RenameConversation(BaseModel):
    title: str = Field(max_length=200)


def conversation_view(conversation: Conversation) -> dict:
    return {"id": conversation.id, "title": conversation.title,
            "createdAt": conversation.created_at.isoformat() if conversation.created_at else None,
            "updatedAt": conversation.updated_at.isoformat() if conversation.updated_at else None}


SelectionName = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")]
# 知识库的全局 id <知识源id>.<知识库id>，或 <知识源id>.* 表示这个来源下的全部
KnowledgeBaseName = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,64}\.([A-Za-z0-9_-]{1,64}|\*)$")]


class SendMessage(BaseModel):
    text: str = Field(min_length=1, max_length=200_000)
    mode: str = Field(default="followup", pattern="^(followup|steer)$")
    # 聊天页选的回答语言；不传时按提问里有没有汉字判断
    language: Literal["zh", "en"] | None = None
    # 本轮带的附件 id（先经附件接口上传到这个会话）；文件已在沙箱 /workspace/input 里，模型按需读
    attachments: list[SelectionName] | None = Field(default=None, max_length=20)
    # 本轮选择的技能与知识库；不传表示没有选（技能不标选中、知识用全部可查的）
    skills: list[SelectionName] | None = Field(default=None, max_length=MAX_SELECTED)
    knowledge_bases: list[KnowledgeBaseName] | None = Field(default=None, max_length=MAX_SELECTED)

    def selection(self) -> TurnSelection:
        return TurnSelection(skills=None if self.skills is None else tuple(self.skills),
                             knowledge_bases=None if self.knowledge_bases is None else tuple(self.knowledge_bases))


class Decision(BaseModel):
    allow: bool
    # session：「本会话内同类操作不再询问」，只在批准时有效
    scope: Literal["once", "session"] = "once"


class AnswerItem(BaseModel):
    id: str = Field(min_length=1, max_length=256)
    selected: list[Annotated[str, Field(min_length=1, max_length=1000)]] = Field(default_factory=list, max_length=64)
    custom: str | None = Field(default=None, max_length=4000)


class Answer(BaseModel):
    """一次提问的回答，每题一条。选项是否存在、单选多选由宿主按原问题核对。"""

    answers: list[AnswerItem] = Field(min_length=1, max_length=64)

    def contract(self) -> QuestionAnswer:
        return QuestionAnswer(answers=tuple(QuestionAnswerItem(id=a.id, selected=tuple(a.selected), custom=a.custom)
                                            for a in self.answers))


QUESTION_ERRORS = {
    "question_not_found": (404, "这个问题已经结束，不能再回答"),
    "invalid_answer": (400, "回答和问题对不上，请刷新页面后重试"),
    "reply_queued": (409, "回答已经提交，正在等 Agent 接收"),
}


def bearer_matches(authorization: str | None, expected: str) -> bool:
    """先确认 `Bearer ` 前缀，再按字节常量时间比较；任何字符（含非 ASCII）都不会抛异常。"""
    if not authorization or not authorization.startswith("Bearer "):
        return False
    presented = authorization[7:].encode("utf-8", "surrogateescape")
    return hmac.compare_digest(presented, expected.encode("utf-8"))


def upstream_error(error: Exception, action: str) -> HTTPException:
    if isinstance(error, SessionBusy):
        return HTTPException(status_code=409, detail={"code": "session_busy", "message": "会话正忙，请稍后重试"})
    if isinstance(error, SessionNotFound):
        return HTTPException(status_code=404, detail={"code": "session_not_running", "message": "会话当前没有在运行"})
    code = error.code if isinstance(error, KernelError) else "host_unreachable"
    log.warning("%s failed: %s %s", action, type(error).__name__, code)
    return HTTPException(status_code=502, detail={"code": "runtime_unavailable", "message": "运行时暂时不可用，请稍后重试"})


HOST_ERRORS = (KernelError, httpx.HTTPError, OSError)
CREDENTIAL_ERRORS = (ToolAccessRevoked, ToolCredentialUnavailable, ProfileUnavailable)


def credential_error(error: Exception) -> HTTPException:
    if isinstance(error, ToolAccessRevoked):
        return HTTPException(status_code=403, detail={"code": "not_a_member", "message": "你已不是本工作空间的成员"})
    if isinstance(error, ProfileUnavailable):
        return HTTPException(status_code=503, detail={"code": "runtime_profile_unavailable",
                                                      "message": "运行配置暂时不可用，请稍后重试"})
    return HTTPException(status_code=503, detail={"code": "tool_credentials_unavailable",
                                                  "message": "工具服务暂时不可用，请稍后重试"})


def build_router(service: ConversationService, current_actor: ActorDependency) -> APIRouter:
    router = APIRouter(prefix="/api/conversations")
    Who = Annotated[Actor, Depends(current_actor)]

    async def owned(conversation_id: str, who: Actor) -> Conversation:
        try:
            return await service.get(conversation_id=conversation_id, workspace_id=who.workspace_id, user_id=who.user_id)
        except ConversationNotFound as error:
            raise HTTPException(status_code=404, detail="会话不存在") from error

    @router.post("", status_code=201)
    async def create(body: CreateConversation, who: Who):
        try:
            conversation = await service.create(workspace_id=who.workspace_id, user_id=who.user_id, title=body.title)
        except CREDENTIAL_ERRORS as error:
            raise credential_error(error) from error
        except HOST_ERRORS as error:
            raise upstream_error(error, "create conversation") from error
        return {"id": conversation.id, "title": conversation.title}

    @router.get("")
    async def list_conversations(who: Who, limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 30,
                                 before: datetime | None = None):
        page = await service.list_page(workspace_id=who.workspace_id, user_id=who.user_id, limit=limit, before=before)
        return {"items": [conversation_view(c) for c in page.items],
                "next": page.next.isoformat() if page.next else None}

    @router.patch("/{conversation_id}")
    async def rename(conversation_id: str, body: RenameConversation, who: Who):
        conversation = await owned(conversation_id, who)
        return conversation_view(await service.rename(conversation, body.title))

    @router.delete("/{conversation_id}", status_code=204)
    async def remove(conversation_id: str, who: Who):
        conversation = await owned(conversation_id, who)
        try:
            await service.delete(conversation)
        except HOST_ERRORS as error:
            raise upstream_error(error, "delete conversation") from error
        return Response(status_code=204)

    @router.post("/{conversation_id}/messages", status_code=202)
    async def send(conversation_id: str, body: SendMessage, who: Who):
        conversation = await owned(conversation_id, who)
        turn = TurnInput(text=body.text, mode=body.mode, language=body.language)
        try:
            message_id = await service.send(conversation, turn, body.selection(),
                                            tuple(body.attachments or ()))
        except AttachmentUnavailable as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except CREDENTIAL_ERRORS as error:
            raise credential_error(error) from error
        except HOST_ERRORS as error:
            raise upstream_error(error, "send") from error
        return {"messageId": message_id}

    @router.post("/{conversation_id}/cancel")
    async def cancel(conversation_id: str, who: Who):
        conversation = await owned(conversation_id, who)
        try:
            result = await service.cancel(conversation)
        except HOST_ERRORS as error:
            raise upstream_error(error, "cancel") from error
        return {"settled": result.settled, "status": result.status}

    @router.get("/{conversation_id}/approvals")
    async def approvals(conversation_id: str, who: Who):
        conversation = await owned(conversation_id, who)
        try:
            pending = await service.approvals(conversation)
        except HOST_ERRORS as error:
            raise upstream_error(error, "list approvals") from error
        return [a.model_dump(exclude={"schema_version"}) for a in pending]

    @router.post("/{conversation_id}/approvals/{approval_id}")
    async def decide(conversation_id: str, body: Decision, who: Who, approval_id: Annotated[str, APPROVAL_ID]):
        conversation = await owned(conversation_id, who)
        annotate(allow=body.allow, scope=body.scope)
        try:
            await service.decide(conversation, approval_id, body.allow, remember=body.allow and body.scope == "session")
        except KernelError as error:
            if error.status == 404:
                raise HTTPException(status_code=404, detail="审批不存在或已结束") from error
            raise upstream_error(error, "decide approval") from error
        except (httpx.HTTPError, OSError) as error:
            raise upstream_error(error, "decide approval") from error
        return {"approvalId": approval_id, "allow": body.allow, "scope": body.scope}

    @router.get("/{conversation_id}/questions")
    async def questions(conversation_id: str, who: Who):
        conversation = await owned(conversation_id, who)
        try:
            pending = await service.questions(conversation)
        except HOST_ERRORS as error:
            raise upstream_error(error, "list questions") from error
        return [q.model_dump(exclude={"schema_version": True, "questions": {"__all__": {
            "schema_version": True, "options": {"__all__": {"schema_version"}}}}}) for q in pending]

    @router.post("/{conversation_id}/questions/{question_id}")
    async def answer(conversation_id: str, body: Answer, who: Who, question_id: Annotated[str, QUESTION_ID]):
        conversation = await owned(conversation_id, who)
        annotate(answers=len(body.answers))  # 回答内容可能敏感，审计只记题数
        try:
            await service.answer(conversation, question_id, body.contract())
        except CREDENTIAL_ERRORS as error:
            raise credential_error(error) from error
        except KernelError as error:
            if error.code in QUESTION_ERRORS:
                status, message = QUESTION_ERRORS[error.code]
                log.info("answer question %s rejected by host: %s", question_id, error)
                raise HTTPException(status_code=status, detail={"code": error.code, "message": message}) from error
            raise upstream_error(error, "answer question") from error
        except (httpx.HTTPError, OSError) as error:
            raise upstream_error(error, "answer question") from error
        return {"questionId": question_id}

    @router.get("/{conversation_id}/events")
    async def events(conversation_id: str, who: Who, after: int = 0,
                     last_event_id: Annotated[str | None, Header()] = None):
        conversation = await owned(conversation_id, who)
        feed = service.events(conversation, parse_cursor(last_event_id, after))

        async def stream() -> AsyncIterator[dict]:
            async for item in feed:
                yield {"id": item.sse_id, "event": item.type, "data": json.dumps(item.payload, ensure_ascii=False)}

        return EventSourceResponse(stream(), ping=15)

    return router
