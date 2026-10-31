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
from pydantic import BaseModel, ConfigDict, Field, model_validator
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
from ..profile.snapshot import MAX_DROPPED, MAX_SELECTED, NO_SELECTION, TurnSelection
from .feedback import MAX_NOTE, RUN_ID, Feedback, FeedbackStore
from .mentions import MAX_MENTIONS, Mention, mentioned, turn_mentions
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



class RenameConversation(BaseModel):
    title: str = Field(max_length=200)


def conversation_view(conversation: Conversation) -> dict:
    return {"id": conversation.id, "title": conversation.title,
            "createdAt": conversation.created_at.isoformat() if conversation.created_at else None,
            "updatedAt": conversation.updated_at.isoformat() if conversation.updated_at else None}


SelectionName = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")]
# 知识库的全局 id <知识源id>.<知识库id>，或 <知识源id>.* 表示这个来源下的全部
KnowledgeBaseName = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,64}\.([A-Za-z0-9_-]{1,64}|\*)$")]
# 检索条件的写法如「部门 = 财务部」「标签：简报」，原样比对，不解析
ConditionLabel = Annotated[str, Field(min_length=1, max_length=500)]


class TurnChoice(BaseModel):
    """一轮的选择：技能、知识库、撤掉的检索条件、@，以及回答方式。发消息时带；新建会话时也可以带上第一条消息的，
    建会话就按它定运行配置，第一条消息不用先开再重开。"""

    # 本轮选择的技能与知识库；不传表示没有选（技能不标选中、知识用全部可查的）
    skills: list[SelectionName] | None = Field(default=None, max_length=MAX_SELECTED)
    knowledge_bases: list[KnowledgeBaseName] | None = Field(default=None, max_length=MAX_SELECTED)
    # 用户在上一轮回答上方撤掉的检索条件（照检索结果里的写法交回），这一轮检索时去掉
    dropped_conditions: list[ConditionLabel] = Field(default_factory=list, max_length=MAX_DROPPED)
    # 输入框里 @ 的知识库、目录、文件、标签、技能（见 mentions.py）
    mentions: list[Mention] = Field(default_factory=list, max_length=MAX_MENTIONS)
    # 回答方式：智能体，或只查知识库的快速问答（宿主只放行 knowledge_search、knowledge_read）；用户在输入框里随时切换
    answer_mode: Literal["agent", "quick"] = "agent"

    def _scoped(self) -> tuple[list[str] | None, list[Mention]]:
        """这一轮算数的技能与 @：快速问答用不了技能（宿主拦下 skill），选了也不算，免得技能目录把它标成选中。"""
        if self.answer_mode == "quick":
            return None, [m for m in self.mentions if m.type != "skill"]
        return self.skills, self.mentions

    def selection(self) -> TurnSelection:
        skills, mentions = self._scoped()
        chosen = TurnSelection(skills=None if skills is None else tuple(skills),
                               knowledge_bases=None if self.knowledge_bases is None else tuple(self.knowledge_bases),
                               dropped_conditions=tuple(self.dropped_conditions))
        return mentioned(chosen, mentions)


class CreateConversation(BaseModel):
    title: str = Field(default="", max_length=200)
    # 第一条消息的选择（可选）：带了就按它绑定运行配置
    first_turn: TurnChoice | None = None


class SendMessage(TurnChoice):
    text: str = Field(min_length=1, max_length=200_000)
    mode: str = Field(default="followup", pattern="^(followup|steer)$")
    # 聊天页选的回答语言；不传时按提问里有没有汉字判断
    language: Literal["zh", "en"] | None = None
    # 本轮带的附件 id（先经附件接口上传到这个会话）；文件已在沙箱 /workspace/input 里，模型按需读
    attachments: list[SelectionName] | None = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def _quick_has_no_files(self) -> "SendMessage":
        # 快速问答没有读文件的工具，附件放进会话容器也没人读
        if self.answer_mode == "quick" and self.attachments:
            raise ValueError("快速问答只查知识库，不能带附件；要处理附件请切换到智能体")
        return self

    def turn(self) -> TurnInput:
        # 插话进行中的回合，新的选择留到下一轮才生效（service.send），@ 也就不写进这条消息，免得模型以为已经限定了
        mentions = turn_mentions(self._scoped()[1]) if self.mode == "followup" else ()
        return TurnInput(text=self.text, mode=self.mode, language=self.language, mentions=mentions,
                         answer_mode=self.answer_mode)


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


class FeedbackBody(BaseModel):
    """对一轮回答的评分（照 DSH 官方的消息反馈）：赞或踩必填，分类与说明可选。"""

    model_config = ConfigDict(extra="forbid")
    rating: Literal["like", "dislike"]
    category: Literal["task-result", "instruction-following", "product-interaction", "service-stability",
                      "resource-cost", "security-privacy-permission", "other"] | None = None
    note: str | None = Field(default=None, max_length=MAX_NOTE)


def feedback_view(item: Feedback) -> dict:
    return {"runId": item.run_id, "rating": item.rating, "category": item.category, "note": item.note}


def build_router(service: ConversationService, current_actor: ActorDependency,
                 feedback: FeedbackStore | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/workspaces/{workspace_id}/conversations")
    Who = Annotated[Actor, Depends(current_actor)]

    async def owned(conversation_id: str, who: Actor) -> Conversation:
        try:
            return await service.get(conversation_id=conversation_id, workspace_id=who.workspace_id, user_id=who.user_id)
        except ConversationNotFound as error:
            raise HTTPException(status_code=404, detail="会话不存在") from error

    @router.post("", status_code=201)
    async def create(body: CreateConversation, who: Who):
        """新建对话"""
        try:
            selection = body.first_turn.selection() if body.first_turn else NO_SELECTION
            conversation = await service.create(workspace_id=who.workspace_id, user_id=who.user_id, title=body.title,
                                                selection=selection)
        except CREDENTIAL_ERRORS as error:
            raise credential_error(error) from error
        except HOST_ERRORS as error:
            raise upstream_error(error, "create conversation") from error
        return {"id": conversation.id, "title": conversation.title}

    @router.get("")
    async def list_conversations(who: Who, limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 30,
                                 before: datetime | None = None):
        """列出我的对话"""
        page = await service.list_page(workspace_id=who.workspace_id, user_id=who.user_id, limit=limit, before=before)
        return {"items": [conversation_view(c) for c in page.items],
                "next": page.next.isoformat() if page.next else None}

    @router.get("/search")
    async def search(who: Who, q: Annotated[str, Query(min_length=1, max_length=100)]):
        """侧栏的搜索：标题与每轮问的话里有 q 的对话，最近有动静的在前；snippet 是命中的那句问话的一段。"""
        hits = await service.search(workspace_id=who.workspace_id, user_id=who.user_id, query=q)
        return {"items": [{**conversation_view(h.conversation), "snippet": h.snippet} for h in hits]}

    @router.patch("/{conversation_id}")
    async def rename(conversation_id: str, body: RenameConversation, who: Who):
        """对话改名"""
        conversation = await owned(conversation_id, who)
        return conversation_view(await service.rename(conversation, body.title))

    @router.delete("/{conversation_id}", status_code=204)
    async def remove(conversation_id: str, who: Who):
        """删除对话"""
        conversation = await owned(conversation_id, who)
        try:
            await service.delete(conversation)
        except HOST_ERRORS as error:
            raise upstream_error(error, "delete conversation") from error
        return Response(status_code=204)

    @router.post("/{conversation_id}/messages", status_code=202)
    async def send(conversation_id: str, body: SendMessage, who: Who):
        """发消息（提问或插话）"""
        conversation = await owned(conversation_id, who)
        try:
            message_id = await service.send(conversation, body.turn(), body.selection(),
                                            tuple(body.attachments or ()))
        except AttachmentUnavailable as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except CREDENTIAL_ERRORS as error:
            raise credential_error(error) from error
        except HOST_ERRORS as error:
            raise upstream_error(error, "send") from error
        return {"messageId": message_id}

    if feedback is not None:
        RunId = Annotated[str, Path(pattern=RUN_ID.pattern)]

        @router.get("/{conversation_id}/feedback")
        async def list_feedback(conversation_id: str, who: Who):
            """我对回答的评价"""
            conversation = await owned(conversation_id, who)
            return {"items": [feedback_view(item) for item in await feedback.list(conversation)]}

        @router.put("/{conversation_id}/feedback/{run_id}")
        async def put_feedback(conversation_id: str, run_id: RunId, body: FeedbackBody, who: Who):
            """评价一次回答"""
            conversation = await owned(conversation_id, who)
            return feedback_view(await feedback.put(conversation, run_id, body.rating, body.category, body.note))

        @router.delete("/{conversation_id}/feedback/{run_id}", status_code=204)
        async def delete_feedback(conversation_id: str, run_id: RunId, who: Who):
            """撤回评价"""
            await feedback.delete(await owned(conversation_id, who), run_id)
            return Response(status_code=204)

    @router.post("/{conversation_id}/cancel")
    async def cancel(conversation_id: str, who: Who):
        """停止正在进行的回答"""
        conversation = await owned(conversation_id, who)
        try:
            result = await service.cancel(conversation)
        except HOST_ERRORS as error:
            raise upstream_error(error, "cancel") from error
        return {"settled": result.settled, "status": result.status}

    @router.get("/{conversation_id}/approvals")
    async def approvals(conversation_id: str, who: Who):
        """待审批的操作"""
        conversation = await owned(conversation_id, who)
        try:
            pending = await service.approvals(conversation)
        except HOST_ERRORS as error:
            raise upstream_error(error, "list approvals") from error
        return [a.model_dump(exclude={"schema_version"}) for a in pending]

    @router.post("/{conversation_id}/approvals/{approval_id}")
    async def decide(conversation_id: str, body: Decision, who: Who, approval_id: Annotated[str, APPROVAL_ID]):
        """批准或拒绝一个操作"""
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
        """智能体在等我回答的问题"""
        conversation = await owned(conversation_id, who)
        try:
            pending = await service.questions(conversation)
        except HOST_ERRORS as error:
            raise upstream_error(error, "list questions") from error
        return [q.model_dump(exclude={"schema_version": True, "questions": {"__all__": {
            "schema_version": True, "options": {"__all__": {"schema_version"}}}}}) for q in pending]

    @router.post("/{conversation_id}/questions/{question_id}")
    async def answer(conversation_id: str, body: Answer, who: Who, question_id: Annotated[str, QUESTION_ID]):
        """回答智能体的问题"""
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
        """对话事件流（SSE）"""
        conversation = await owned(conversation_id, who)
        feed = service.events(conversation, parse_cursor(last_event_id, after))

        async def stream() -> AsyncIterator[dict]:
            async for item in feed:
                data = json.dumps(item.payload, ensure_ascii=False)
                # 逐字片段不带 id：浏览器的 Last-Event-ID 只停在落库的事件上
                yield ({"event": item.type, "data": data} if item.sse_id is None
                       else {"id": item.sse_id, "event": item.type, "data": data})

        return EventSourceResponse(stream(), ping=15)

    return router
