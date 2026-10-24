"""会话服务：建会话、发消息、取消、审批、回答提问、事件流。

- 建会话先写库再在宿主上打开；打开失败就删掉刚写的记录，不留孤儿；
- 宿主侧会话丢失时（宿主重启）发送方以恢复方式重新打开，并先补发模型令牌再发送；
  同一会话的恢复按会话串行，并发发送不会互相撞上 session_busy；
- 每轮开始时重算运行配置；版本与会话记住的上一版不同，就在本轮开始前释放宿主会话、
  以新策略恢复打开（带上已完成的历史），再发送。本该「分叉出后继会话」，
  宿主还没有分叉接口，所以先用恢复代替：同一会话 id、历史完整、换上新的工具策略与令牌；
- 回答提问时宿主侧会话不在（宿主重启过），同样先以恢复方式打开再交回答：官方把中断的提问记成未知结果，
  问题转入 continued，回答会开新一轮。回答不触发「配置变了就重开」：重开会把正在前台等待的提问取消掉；
- API 层只经这里拿事件流，不接触泵与表。
"""

import asyncio
import logging
import uuid
import weakref
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, TypeVar

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..kernel.contract import (
    AgentKernel,
    CancelResult,
    KnowledgeCatalog,
    PendingApproval,
    PendingQuestion,
    QuestionAnswer,
    SessionBusy,
    SessionNotFound,
    SessionPolicy,
    SessionSpec,
    TurnAttachment,
    TurnInput,
)
from ..profile.snapshot import NO_SELECTION, RuntimeProfile, TurnSelection
from .models import Conversation, RunEvent
from .pump import EventPump
from .replay import Cursor, EventFeed, Outgoing
from .tool_credentials import ToolCredentials

# 删除会话时一并清掉的关联数据（引用目录、动作回执等），由装配方按模块给出
Eraser = Callable[[Conversation], Awaitable[None]]
MAX_PAGE = 100


@dataclass(frozen=True)
class ConversationPage:
    items: tuple[Conversation, ...]
    next: datetime | None

log = logging.getLogger(__name__)

RESUME_RETRY_DELAYS = (0.1, 0.3, 1.0)
CATALOG_TIMEOUT = 3.0  # 知识库目录最多等这么久（KnowFlow 要远程列库），超了这轮就不带，宿主沿用上一轮的
# 本轮的知识库目录：按会话发起人与运行配置里的知识范围算
KnowledgeCatalogOf = Callable[[Conversation, RuntimeProfile], Awaitable[KnowledgeCatalog]]

T = TypeVar("T")


class ConversationNotFound(Exception):
    pass


class ProfileProvider(Protocol):
    """运行配置的编译与绑定（实现见 profile.runtime.ConversationProfiles）。"""

    async def compile(self, conversation: Conversation, selection: TurnSelection = NO_SELECTION) -> RuntimeProfile: ...

    async def bound_version(self, conversation: Conversation) -> str | None: ...

    async def bind(self, conversation: Conversation, profile: RuntimeProfile) -> None: ...


class AttachmentUnavailable(Exception):
    """本轮要带的附件不属于这个会话、还没放进沙箱或已失效。message 可以直接给用户看。"""


class AttachmentSource(Protocol):
    """本轮消息带的附件（实现见 attachments.turns.TurnAttachments），不可用时抛 AttachmentUnavailable。"""

    async def for_turn(self, conversation: Conversation, ids: tuple[str, ...]) -> tuple[TurnAttachment, ...]: ...


class GrantRecorder(Protocol):
    """会话审批授权（实现见 approvals.SessionGrants）。"""

    async def remember(self, workspace_id: str, conversation_id: str, user_id: str, tool_name: str) -> bool: ...


class ConversationService:
    def __init__(self, kernel: AgentKernel, sessionmaker: async_sessionmaker, pump: EventPump, feed: EventFeed, *,
                 on_host_restart: Callable[[], Awaitable[None]] | None = None,
                 refresh_model_credential: Callable[[], Awaitable[None]] | None = None,
                 tool_credentials: ToolCredentials | None = None,
                 skill_credentials: ToolCredentials | None = None,
                 erasers: tuple[Eraser, ...] = (),
                 profiles: ProfileProvider | None = None,
                 grants: GrantRecorder | None = None,
                 attachments: AttachmentSource | None = None,
                 knowledge_catalog: KnowledgeCatalogOf | None = None,
                 resume_retry_delays: tuple[float, ...] = RESUME_RETRY_DELAYS) -> None:
        self._kernel = kernel
        self._sessionmaker = sessionmaker
        self._pump = pump
        self._feed = feed
        self._on_host_restart = on_host_restart
        self._refresh_model_credential = refresh_model_credential
        self._resume_retry_delays = resume_retry_delays
        self._tool_credentials = tool_credentials
        self._skill_credentials = skill_credentials
        self._erasers = erasers
        self._profiles = profiles
        self._grants = grants
        self._attachments = attachments
        self._knowledge_catalog = knowledge_catalog
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()

    @staticmethod
    def policy_for(profile: RuntimeProfile | None) -> SessionPolicy:
        """宿主策略取自编译出的运行配置：deny 是用户此刻无权使用的工具名。没装配编译器时不收窄。"""
        return SessionPolicy() if profile is None else profile.policy()

    async def _profile(self, conversation: Conversation, selection: TurnSelection) -> RuntimeProfile | None:
        if self._profiles is None:
            return None
        return await self._profiles.compile(conversation, selection)

    async def _bind(self, conversation: Conversation, profile: RuntimeProfile | None) -> None:
        if self._profiles is not None and profile is not None:
            await self._profiles.bind(conversation, profile)

    async def _spec(self, conversation: Conversation, *, resume: bool,
                    profile: RuntimeProfile | None = None) -> SessionSpec:
        token = None
        if self._tool_credentials is not None:
            token = await self._tool_credentials.token_for(conversation)
        skills_token = None
        if self._skill_credentials is not None:
            skills_token = await self._skill_credentials.token_for(conversation)
        return SessionSpec(session_id=conversation.id, policy=self.policy_for(profile), resume=resume,
                           tools_token=token, skills_token=skills_token)

    def _opened(self, conversation: Conversation) -> None:
        for credentials in (self._tool_credentials, self._skill_credentials):
            if credentials is not None:
                credentials.track(conversation)

    async def create(self, *, workspace_id: str, user_id: str, title: str = "",
                     selection: TurnSelection = NO_SELECTION) -> Conversation:
        conversation = Conversation(id=f"c_{uuid.uuid4().hex}", workspace_id=workspace_id, user_id=user_id, title=title)
        async with self._sessionmaker() as session:
            session.add(conversation)
            await session.commit()
        try:
            profile = await self._profile(conversation, selection)
            await self._kernel.open_session(await self._spec(conversation, resume=False, profile=profile))
            await self._bind(conversation, profile)
        except BaseException:
            await self._forget(conversation.id)
            raise
        self._opened(conversation)
        self._pump.ensure(conversation.id, workspace_id)
        return conversation

    async def _forget(self, conversation_id: str) -> None:
        try:
            async with self._sessionmaker() as session:
                await session.execute(delete(Conversation).where(Conversation.id == conversation_id))
                await session.commit()
        except Exception:
            log.exception("failed to remove conversation %s after host open failure", conversation_id)

    async def get(self, *, conversation_id: str, workspace_id: str, user_id: str) -> Conversation:
        async with self._sessionmaker() as session:
            conversation = (await session.execute(select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.workspace_id == workspace_id,
                Conversation.user_id == user_id,
            ))).scalar_one_or_none()
        if conversation is None:
            raise ConversationNotFound(conversation_id)
        return conversation

    async def find(self, *, conversation_id: str, workspace_id: str) -> Conversation:
        """不限所属用户地取会话（管理端查看运行记录用，调用方自己判权限）。"""
        async with self._sessionmaker() as session:
            conversation = (await session.execute(select(Conversation).where(
                Conversation.id == conversation_id, Conversation.workspace_id == workspace_id,
            ))).scalar_one_or_none()
        if conversation is None:
            raise ConversationNotFound(conversation_id)
        return conversation

    def _lock(self, conversation_id: str) -> asyncio.Lock:
        lock = self._locks.get(conversation_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[conversation_id] = lock
        return lock

    async def send(self, conversation: Conversation, turn: TurnInput,
                   selection: TurnSelection = NO_SELECTION, attachment_ids: tuple[str, ...] = ()) -> str:
        if attachment_ids:
            if self._attachments is None:
                raise AttachmentUnavailable("附件功能没有开启")
            turn = turn.model_copy(update={"attachments": await self._attachments.for_turn(conversation, attachment_ids)})
        lock = self._lock(conversation.id)
        async with lock:
            profile = await self._profile(conversation, selection)
            # 插话（steer）发生在进行中的回合里，重开会话会打断它：新配置留到下一轮生效，执行时的重判照样拦得住
            if (profile is not None and turn.mode == "followup"
                    and await self._profiles.bound_version(conversation) != profile.version):
                await self._reopen(conversation, profile)
            await self._refresh_credentials(conversation)
            turn = await self._with_catalog(conversation, turn, profile)
            try:
                message_id = await self._kernel.send(conversation.id, turn)
            except SessionNotFound:
                message_id = await self._resume_and_send(conversation, turn, profile)
        await self._touch(conversation.id)
        self._pump.ensure(conversation.id, conversation.workspace_id)
        return message_id

    async def _with_catalog(self, conversation: Conversation, turn: TurnInput,
                            profile: RuntimeProfile | None) -> TurnInput:
        """带上本轮的知识库目录。算不出来（知识源慢、出错）就不带，宿主沿用上一轮的，不挡这一轮。"""
        if self._knowledge_catalog is None or profile is None:
            return turn
        try:
            catalog = await asyncio.wait_for(self._knowledge_catalog(conversation, profile), CATALOG_TIMEOUT)
        except Exception:
            log.warning("knowledge catalog for conversation %s unavailable", conversation.id, exc_info=True)
            return turn
        return turn.model_copy(update={"knowledge": catalog})

    async def _refresh_credentials(self, conversation: Conversation) -> None:
        """每轮开始前把到期的令牌续上：电脑刚从睡眠醒来时，续期循环可能还没轮到，令牌却已按墙上时间过期。
        这里的失败只记日志，不挡这一轮（令牌没过期就照常能用）。"""
        if self._refresh_model_credential is not None:
            await self._refresh_model_credential()
        for credentials in (self._tool_credentials, self._skill_credentials):
            if credentials is not None:
                await credentials.ensure_fresh(conversation.id)

    async def _touch(self, conversation_id: str, **values) -> None:
        async with self._sessionmaker() as session:
            await session.execute(update(Conversation).where(Conversation.id == conversation_id)
                                  .values(updated_at=datetime.now(UTC), **values))
            await session.commit()

    async def list_page(self, *, workspace_id: str, user_id: str, limit: int = 30,
                   before: datetime | None = None) -> ConversationPage:
        """本人的会话，最近有动静的在前；next 交回给 before 取下一页。"""
        limit = max(1, min(limit, MAX_PAGE))
        query = (select(Conversation)
                 .where(Conversation.workspace_id == workspace_id, Conversation.user_id == user_id)
                 .order_by(Conversation.updated_at.desc(), Conversation.id.desc()).limit(limit + 1))
        if before is not None:
            query = query.where(Conversation.updated_at < before)
        async with self._sessionmaker() as session:
            rows = tuple((await session.execute(query)).scalars().all())
        page = rows[:limit]
        return ConversationPage(items=page, next=page[-1].updated_at if len(rows) > limit else None)

    async def rename(self, conversation: Conversation, title: str) -> Conversation:
        clean = " ".join(title.split())[:200]
        await self._touch(conversation.id, title=clean)
        return await self.get(conversation_id=conversation.id, workspace_id=conversation.workspace_id,
                              user_id=conversation.user_id)

    async def delete(self, conversation: Conversation) -> None:
        """释放宿主会话、停泵、停止续期，再删事件、关联数据与会话本身。宿主上已不在也照删。"""
        try:
            await self._kernel.dispose(conversation.id)
        except SessionNotFound:
            pass
        await self._pump.stop(conversation.id)
        if self._tool_credentials is not None:
            self._tool_credentials.forget(conversation.id)
        for erase in self._erasers:
            await erase(conversation)
        async with self._sessionmaker() as session:
            await session.execute(delete(RunEvent).where(RunEvent.conversation_id == conversation.id))
            await session.execute(delete(Conversation).where(Conversation.id == conversation.id))
            await session.commit()

    async def _reopen(self, conversation: Conversation, profile: RuntimeProfile) -> None:
        """配置版本变了：释放宿主会话，以新策略恢复打开，再记下新版本。

        打开失败时不记新版本：下一轮还会再试；宿主上会话已不在，发送会走 SessionNotFound 的恢复路径。
        """
        log.info("runtime profile of %s changed to %s; reopening host session", conversation.id, profile.version[:12])
        try:
            await self._kernel.dispose(conversation.id)
        except SessionNotFound:
            pass
        await self._kernel.open_session(await self._spec(conversation, resume=True, profile=profile))
        self._opened(conversation)
        await self._bind(conversation, profile)

    async def _resume_and_send(self, conversation: Conversation, turn: TurnInput,
                               profile: RuntimeProfile | None = None) -> str:
        return await self._resume_then(conversation, profile, lambda: self._kernel.send(conversation.id, turn))

    async def _resume_then(self, conversation: Conversation, profile: RuntimeProfile | None,
                           action: Callable[[], Awaitable[T]]) -> T:
        """宿主侧会话不在：以恢复方式打开再执行 action；宿主上仍找不到会话时按退避重试。"""
        spec = await self._spec(conversation, resume=True, profile=profile)
        for delay in (*self._resume_retry_delays, None):
            try:
                await self._kernel.open_session(spec)
                self._opened(conversation)
                await self._bind(conversation, profile)
                await self._host_restarted()
            except SessionBusy:
                pass  # 宿主上正在打开（或刚被别人打开）：稍等后直接执行
            try:
                return await action()
            except SessionNotFound:
                if delay is None:
                    raise
            await asyncio.sleep(delay)
        raise AssertionError("unreachable")

    async def _host_restarted(self) -> None:
        if self._on_host_restart is None:
            return
        try:
            await self._on_host_restart()
        except Exception:
            log.exception("host restart hook failed")

    def events(self, conversation: Conversation, cursor: Cursor) -> AsyncIterator[Outgoing]:
        """打开事件流：确保泵在跑，再交出回放 + 实时的迭代器。"""
        self._pump.ensure(conversation.id, conversation.workspace_id)
        return self._feed.iterate(conversation.id, cursor)

    async def cancel(self, conversation: Conversation) -> CancelResult:
        return await self._kernel.cancel(conversation.id)

    async def approvals(self, conversation: Conversation) -> list[PendingApproval]:
        return await self._kernel.pending_approvals(conversation.id)

    async def decide(self, conversation: Conversation, approval_id: str, allow: bool, *,
                     remember: bool = False) -> None:
        """交回审批决定。remember 为真（批准并勾了「本会话内同类操作不再询问」）时先记授权，
        审批人就是对话所属用户（接口层已校验）；之后同一工具的审批由宿主应答者先查授权，不再弹卡片。"""
        if remember and self._grants is not None:
            pending = next((a for a in await self._kernel.pending_approvals(conversation.id) if a.id == approval_id),
                           None)
            if pending is not None:
                await self._grants.remember(conversation.workspace_id, conversation.id, conversation.user_id,
                                            pending.tool_name)
        await self._kernel.decide_approval(conversation.id, approval_id, allow)

    async def questions(self, conversation: Conversation) -> list[PendingQuestion]:
        return await self._kernel.pending_questions(conversation.id)

    async def answer(self, conversation: Conversation, question_id: str, answer: QuestionAnswer) -> None:
        """交回用户对一次提问的回答。与发送共用会话锁，宿主恢复会话时不会和发送撞上。"""
        async with self._lock(conversation.id):
            try:
                await self._kernel.answer_question(conversation.id, question_id, answer)
            except SessionNotFound:
                profile = await self._profile(conversation, NO_SELECTION)
                await self._resume_then(conversation, profile,
                                        lambda: self._kernel.answer_question(conversation.id, question_id, answer))
        await self._touch(conversation.id)
        self._pump.ensure(conversation.id, conversation.workspace_id)
