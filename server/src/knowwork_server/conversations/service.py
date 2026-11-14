"""会话服务：建会话、发消息、取消、审批、回答提问、事件流。

- 建会话先写库再在宿主上打开；打开失败就删掉刚写的记录，不留孤儿；
- 宿主侧会话丢失时（宿主重启）发送方以恢复方式重新打开，并先补发模型令牌再发送；
  同一会话的恢复按会话串行，并发发送不会互相撞上 session_busy；
- 每轮开始时重算运行配置。宿主用得到的部分（工具、被拒工具、技能清单）与会话绑定的上一版不同，就在本轮开始前
  释放宿主会话、以新策略恢复打开（带上已完成的历史），再发送。本该「分叉出后继会话」，宿主还没有分叉接口，
  所以先用恢复代替：同一会话 id、历史完整、换上新的工具策略与令牌。只是本轮选择变了（@ 的知识库、文件、标签、技能，
  撤掉的检索条件）只改绑定、不重开：检索工具执行时从绑定读范围，宿主用不到；而释放会话会结束它还在跑的后台任务、
  结算待决的审批与提问（官方释放会话的行为），不能为每一轮的 @ 付这个代价；
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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..kernel.contract import (
    AgentKernel,
    CancelResult,
    KernelError,
    KnowledgeCatalog,
    ModelUnknown,
    PendingApproval,
    PendingQuestion,
    QuestionAnswer,
    SessionBusy,
    SessionNotFound,
    SessionOwner,
    SessionPolicy,
    SessionSpec,
    TurnAttachment,
    TurnInput,
)
from ..profile.snapshot import NO_SELECTION, RuntimeProfile, TurnSelection
from .answer_modes import AnswerModes, ModeContext
from .constants import DEVICE_SOURCE
from .mention_kinds import MentionKinds
from .models import Conversation, RunEvent
from .pump import EventPump
from .quota import QuotaPolicies, Trigger, TurnStart
from .replay import Cursor, EventFeed, Outgoing
from .scopes import ConversationScopes, ScopeClaim
from .search import SearchHit, search_conversations
from .tool_credentials import ToolCredentials
from .turn_context import TurnContextHook, TurnContextRequest

# 删除会话时一并清掉的关联数据（引用目录、动作回执等），由装配方按模块给出
Eraser = Callable[[Conversation], Awaitable[None]]
# 用户要发消息前预热的东西（会话沙箱容器等），由装配方按模块给出；没配沙箱的部署没有
Warmer = Callable[[Conversation], Awaitable[None]]
MAX_PAGE = 100


@dataclass(frozen=True)
class ConversationPage:
    items: tuple[Conversation, ...]
    next: datetime | None


@dataclass(frozen=True)
class DeviceTurn:
    """在设备上执行的对话这一轮的合同：开会话（设备上的宿主没开着、开着的配置不同就按它对齐；插话时没有）与这一轮。"""

    session: SessionSpec | None
    turn: TurnInput

log = logging.getLogger(__name__)

RESUME_RETRY_DELAYS = (0.1, 0.3, 1.0)
# 宿主「接着记录」打开失败的错误码：DSH 第一轮跑起来才写会话记录，一轮都没开始过的会话也是这个错
OPEN_FAILED = "session_open_failed"
CATALOG_TIMEOUT = 3.0  # 知识库目录最多等这么久（KnowFlow 要远程列库），超了这轮就不带，宿主沿用上一轮的
# 本轮的知识库目录：按会话发起人与运行配置里的知识范围算
KnowledgeCatalogOf = Callable[[Conversation, RuntimeProfile], Awaitable[KnowledgeCatalog]]

T = TypeVar("T")


async def _nothing() -> None:
    """恢复打开之后不用再做什么（激活、预热只要宿主上开着这段对话）。"""


class ConversationNotFound(Exception):
    pass


class RunsOnDevice(Exception):
    """这段对话在用户设备上执行（device.py）：发消息、停止、审批、回答交给那台设备的本机宿主，业务服务不接。"""


class ConversationExists(Exception):
    """指定的对话 id 已经有了（扩展建后台对话时指定 id，见 background.py 的 LocalBackground）。"""


class ProfileProvider(Protocol):
    """运行配置的编译与绑定（实现见 profile.runtime.ConversationProfiles）。"""

    async def compile(self, conversation: Conversation, selection: TurnSelection = NO_SELECTION) -> RuntimeProfile: ...

    async def bound(self, conversation: Conversation) -> RuntimeProfile | None: ...

    async def bind(self, conversation: Conversation, profile: RuntimeProfile) -> None: ...


class AttachmentUnavailable(Exception):
    """本轮要带的附件不属于这个会话、还没放进沙箱或已失效。message 可以直接给用户看。"""


class UnknownModel(Exception):
    """输入框选的模型不存在、停用了，或不是对话模型。"""


class TurnModels(Protocol):
    """输入框能选的对话模型（models.host_sync.HostModelSync 满足它）：核对选的模型，宿主不认识时重推清单。"""

    async def choosable(self, alias: str) -> bool: ...

    async def push(self) -> None: ...


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
                 warmers: tuple[Warmer, ...] = (),
                 profiles: ProfileProvider | None = None,
                 grants: GrantRecorder | None = None,
                 attachments: AttachmentSource | None = None,
                 knowledge_catalog: KnowledgeCatalogOf | None = None,
                 turn_context: TurnContextHook | None = None,
                 answer_modes: AnswerModes | None = None,
                 mention_kinds: MentionKinds | None = None,
                 scopes: ConversationScopes | None = None,
                 quota: QuotaPolicies | None = None,
                 models: TurnModels | None = None,
                 resume_retry_delays: tuple[float, ...] = RESUME_RETRY_DELAYS) -> None:
        self._kernel = kernel
        self._sessionmaker = sessionmaker
        self._pump = pump
        self._feed = feed
        self._on_host_restart = on_host_restart
        self._refresh_model_credential = refresh_model_credential
        self._models = models
        self._resume_retry_delays = resume_retry_delays
        self._tool_credentials = tool_credentials
        self._skill_credentials = skill_credentials
        self._erasers = erasers
        self._warmers = warmers
        self._profiles = profiles
        self._grants = grants
        self._attachments = attachments
        self._knowledge_catalog = knowledge_catalog
        self._turn_context = turn_context
        # 扩展在服务建好之后才登记（app.py）：登记表这时是空的，不能用 or——MentionKinds 空时为假，会换成另一个空表，
        # 后来登记的 @ 类型这里就看不到（@ 了数据源报「不认识这种 @」）
        self._answer_modes = answer_modes if answer_modes is not None else AnswerModes()
        self._mention_kinds = mention_kinds if mention_kinds is not None else MentionKinds()
        self._scopes = scopes if scopes is not None else ConversationScopes()
        self._quota = quota if quota is not None else QuotaPolicies()
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
                           tools_token=token, skills_token=skills_token,
                           owner=SessionOwner(workspace_id=conversation.workspace_id, user_id=conversation.user_id))

    def _opened(self, conversation: Conversation) -> None:
        for credentials in (self._tool_credentials, self._skill_credentials):
            if credentials is not None:
                credentials.track(conversation)

    async def create(self, *, workspace_id: str, user_id: str, title: str = "",
                     selection: TurnSelection = NO_SELECTION, background: bool = False,
                     channel_id: str | None = None, source: str = "", conversation_id: str | None = None,
                     answer_mode: str | None = None, scope: ScopeClaim | None = None) -> Conversation:
        """channel_id、source：从 IM 渠道来的会话记下渠道与平台（channels/service.py），「最近」里打来源标记。
        conversation_id：扩展建后台对话时可以指定，拿自己的记录对上对话；已有同 id 的抛 ConversationExists。
        answer_mode：第一轮的回答方式。不进下拉的（只在扩展页面里用，例如扩展页面右栏里的对话）开的对话不进「最近」和搜索，
        同后台对话：聊天页的下拉里没有这种方式，在那里打开接着发会换成别的方式答；链接照样能打开。
        scope：要挂的东西（例如专家，scopes.py）：建出对话后、编译运行配置前挂上，挂不上抛 ScopeRejected、对话收回。"""
        hidden = background or (answer_mode is not None and not self._answer_modes.listed(answer_mode))
        conversation = Conversation(id=conversation_id or f"c_{uuid.uuid4().hex}", workspace_id=workspace_id,
                                    user_id=user_id, title=title, background=hidden, channel_id=channel_id,
                                    source=source)
        async with self._sessionmaker() as session:
            if conversation_id is not None and await session.get(Conversation, conversation_id) is not None:
                raise ConversationExists(conversation_id)
            session.add(conversation)
            try:
                await session.commit()
            except IntegrityError as error:  # 别的空间有同 id 的（行级安全下看不见），主键撞上
                raise ConversationExists(conversation.id) from error
        try:
            if scope is not None:
                await self._scopes.claim(workspace_id, conversation.id, user_id, scope)
            profile = await self._profile(conversation, selection)
            await self._kernel.open_session(await self._spec(conversation, resume=False, profile=profile))
            await self._bind(conversation, profile)
        except BaseException:
            if scope is not None:
                await self._scopes.release(workspace_id, conversation.id)
            await self._forget(conversation.id)
            raise
        self._opened(conversation)
        self._pump.ensure(conversation.id, workspace_id)
        return conversation

    async def create_on_device(self, *, workspace_id: str, user_id: str, title: str = "",
                               scope: ScopeClaim | None = None) -> Conversation:
        """建一条在用户设备上执行的对话（device.py）：只有这一行，不开宿主会话、不起事件泵、不续令牌。
        scope 同 create：建出来就挂上，挂不上抛 ScopeRejected、对话收回。"""
        conversation = Conversation(id=f"c_{uuid.uuid4().hex}", workspace_id=workspace_id, user_id=user_id,
                                    title=" ".join(title.split())[:200], source=DEVICE_SOURCE)
        async with self._sessionmaker() as session:
            session.add(conversation)
            await session.commit()
            await session.refresh(conversation)
        if scope is not None:
            try:
                await self._scopes.claim(workspace_id, conversation.id, user_id, scope)
            except BaseException:
                await self._scopes.release(workspace_id, conversation.id)
                await self._forget(conversation.id)
                raise
        return conversation

    def _host(self, conversation: Conversation) -> AgentKernel:
        """要在我们宿主上做的事都经这里拿内核（发消息、停止、审批、回答、激活、释放）。在设备上执行的对话（device.py）抛
        RunsOnDevice：它在宿主上没有会话，不能悄悄去宿主上找一个、开一个。新加碰宿主的操作也从这里拿，不用各自记得判断在哪执行
        （tests/test_device_conversations.py 把每个公开方法对设备上的对话都过一遍，钉住不碰宿主）。"""
        if self._on_device(conversation):
            raise RunsOnDevice(conversation.id)
        return self._kernel

    @staticmethod
    def _on_device(conversation: Conversation) -> bool:
        """在用户设备上执行：宿主上没有它的会话，预热、跟事件、待审批与待回答、释放这几样对它都没有要做的。"""
        return conversation.source == DEVICE_SOURCE

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
        """发一轮：准备（和设备上执行的对话共用，见 prepare_on_device）再送给我们的宿主。"""
        self._host(conversation)  # 设备上执行的对话在这里就拒绝，后面的步骤都在宿主上
        # 额度最先判：认领附件会把附件挂进这段对话、放进沙箱，额度用完被拦的一轮不该先占掉附件
        await self._within_quota(conversation)
        turn = await self._checked(conversation, turn, await self._attached(conversation, attachment_ids))
        async with self._lock(conversation.id):
            profile = await self._profile(conversation, selection)
            # 插话（steer）发生在进行中的回合里，重开或改绑定都会打断它：新配置留到下一轮生效，执行时的重判照样拦得住
            if profile is not None and turn.mode == "followup":
                await self._follow(conversation, profile)
            await self._refresh_credentials(conversation)
            turn = await self._packed(conversation, turn, profile)
            try:
                message_id = await self._deliver(conversation, turn, profile)
            except ModelUnknown:
                # 宿主还没收到这个模型（刚重启、还没推清单）：推一次再发；再不认识就让调用方报错
                if self._models is None:
                    raise
                await self._models.push()
                message_id = await self._deliver(conversation, turn, profile)
        await self._touch(conversation.id)
        self._pump.ensure(conversation.id, conversation.workspace_id)
        return message_id

    async def prepare_on_device(self, conversation: Conversation, turn: TurnInput,
                                selection: TurnSelection = NO_SELECTION) -> DeviceTurn:
        """在设备上执行的对话开始一轮（device.py）：和 send 同一套准备——核对模型、回答方式与 @，编译并绑定这一轮的运行配置
        （检索工具经工具桥执行时按它收窄），带上知识库目录与扩展补的上下文——只是不送给我们的宿主，把合同交回去，由设备原样
        交给它本机宿主里同一个收件逻辑。附件在设备上另有来源（本机文件），这里不收。"""
        if not self._on_device(conversation):
            raise ValueError("这段对话不在设备上执行")
        await self._within_quota(conversation)
        turn = await self._checked(conversation, turn, ())
        async with self._lock(conversation.id):
            profile = await self._profile(conversation, selection)
            session = None
            # 插话进的是设备上正在跑的那一轮：同云端，不动会话、不改绑定
            if turn.mode == "followup":
                await self._rebind(conversation, profile)
                session = await self._device_spec(conversation, profile)
            turn = await self._packed(conversation, turn, profile)
        await self._touch(conversation.id)
        return DeviceTurn(session=session, turn=turn)

    async def device_session(self, conversation: Conversation) -> SessionSpec:
        """在设备上执行的对话怎么开会话（设备刚建好这段对话时要）：按这个人此刻的运行配置编译并绑定。"""
        if not self._on_device(conversation):
            raise ValueError("这段对话不在设备上执行")
        async with self._lock(conversation.id):
            profile = await self._profile(conversation, NO_SELECTION)
            await self._rebind(conversation, profile)
            return await self._device_spec(conversation, profile)

    async def _device_spec(self, conversation: Conversation, profile: RuntimeProfile | None) -> SessionSpec:
        """设备上的开会话合同：令牌不在里面（设备按对话自己换，业务服务签的令牌只给我们的宿主用）。resume 同云端恢复打开的
        规矩：业务服务已经收到过回合的，设备上找不到会话记录时报错，不悄悄新开、丢掉前面的上下文。"""
        return SessionSpec(session_id=conversation.id, policy=self.policy_for(profile),
                           resume=await self._has_turns(conversation.id),
                           owner=SessionOwner(workspace_id=conversation.workspace_id, user_id=conversation.user_id))

    async def _rebind(self, conversation: Conversation, profile: RuntimeProfile | None) -> None:
        """配置版本和绑定的不同才改绑定（设备上的对话没有宿主会话要重开：开会话的合同每轮随这一轮交回，设备按它对齐）。"""
        if profile is None or self._profiles is None:
            return
        bound = await self._profiles.bound(conversation)
        if bound is None or bound.version != profile.version:
            await self._bind(conversation, profile)

    async def _within_quota(self, conversation: Conversation, *, trigger: Trigger = "user") -> None:
        """开始一轮之前问扩展登记的额度判定（conversations/quota.py），超了抛 QuotaExceeded。放在一切有副作用的准备之前
        （认领附件、@ 的绑定、开宿主会话），被拦的一轮什么都不留下；用完了后面的也不用算。没登记判定时什么都不调。"""
        await self._quota.before_turn(TurnStart(workspace_id=conversation.workspace_id, user_id=conversation.user_id,
                                                conversation_id=conversation.id, trigger=trigger))

    async def _attached(self, conversation: Conversation, ids: tuple[str, ...]) -> tuple[TurnAttachment, ...]:
        if not ids:
            return ()
        if self._attachments is None:
            raise AttachmentUnavailable("附件功能没有开启")
        return await self._attachments.for_turn(conversation, ids)

    async def _checked(self, conversation: Conversation, turn: TurnInput,
                       attachments: tuple[TurnAttachment, ...]) -> TurnInput:
        """准备这一轮的第一段（不用会话锁）：模型选得对、附件放上、回答方式与 @ 核对过、扩展回答方式的指令算好。
        额度在这之前判（_within_quota）。"""
        if turn.model is not None and self._models is not None and not await self._models.choosable(turn.model):
            raise UnknownModel(turn.model)
        if attachments:
            turn = turn.model_copy(update={"attachments": attachments})
        turn = await self._fixed_mode(conversation, turn)
        turn = await self._with_mentions(conversation, turn)
        return await self._with_answer_mode(conversation, turn)

    async def _packed(self, conversation: Conversation, turn: TurnInput, profile: RuntimeProfile | None) -> TurnInput:
        """准备这一轮的第二段（在会话锁里、运行配置定了之后）：知识库目录与扩展补的上下文。"""
        turn = await self._with_catalog(conversation, turn, profile)
        return await self._with_context(conversation, turn)

    async def _deliver(self, conversation: Conversation, turn: TurnInput, profile: RuntimeProfile | None) -> str:
        try:
            return await self._kernel.send(conversation.id, turn)
        except SessionNotFound:
            return await self._resume_and_send(conversation, turn, profile)

    async def activate(self, conversation: Conversation) -> None:
        """宿主上的插件要在这段对话里自己发起一轮（定时任务到点）：确保宿主上开着它、事件泵开着——宿主随后投进来的
        消息和回答照常记进对话。额度用完了这一次不激活（抛 QuotaExceeded），同用户自己发。"""
        self._host(conversation)  # 设备上执行的对话在这里就拒绝
        await self._within_quota(conversation, trigger="schedule")
        await self._ensure_open(conversation)
        await self._touch(conversation.id)
        self._pump.ensure(conversation.id, conversation.workspace_id)

    async def warm(self, conversation: Conversation) -> None:
        """用户要发消息了（输入框里加了附件）：先把宿主上的对话和沙箱容器备好。两样空闲 30 分钟都会被回收，冷着发一条
        要先重开对话、现建容器、把以前的附件重放进去，多等两三秒。两样同时做；尽力而为，失败只记日志，发送时照常会再开。
        在设备上执行的对话没有可预热的。"""
        if self._on_device(conversation):
            return
        results = await asyncio.gather(self._ensure_open(conversation),
                                       *(warm(conversation) for warm in self._warmers), return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                log.warning("warming conversation %s failed: %s", conversation.id, type(result).__name__,
                            exc_info=result)

    async def _ensure_open(self, conversation: Conversation) -> None:
        """宿主上开着这段对话：不在就按这个人此刻的配置恢复打开（新的策略与令牌），令牌续好。

        开着时不跟着配置重开：可能正跑着一轮，重开会打断它；新配置等用户下次发问时生效，工具执行时的重判照样拦得住。
        和发送共用会话锁：预热和发送撞上时，发送等预热把对话打开再发，不会两边各开一次。"""
        async with self._lock(conversation.id):
            profile = await self._profile(conversation, NO_SELECTION)
            await self._resume_then(conversation, profile, _nothing)
            await self._refresh_credentials(conversation)

    async def _fixed_mode(self, conversation: Conversation, turn: TurnInput) -> TurnInput:
        """扩展给这段对话定了回答方式（scopes.py）：每一轮都按它，输入框里选的不算。取不到范围抛 ScopeUnavailable，
        这一轮不发。"""
        scope = await self._scopes.of(conversation.workspace_id, conversation.id)
        if scope is None or scope.answer_mode is None or scope.answer_mode == turn.answer_mode:
            return turn
        return turn.model_copy(update={"answer_mode": scope.answer_mode})

    async def _with_mentions(self, conversation: Conversation, turn: TurnInput) -> TurnInput:
        """扩展登记的 @（mention_kinds.py）：核对、交给扩展（例如问数把会话绑定到 @ 的数据源）。要在回答方式的指令算出来
        之前，指令里的范围才是这一轮的。核对不过抛 MentionRejected，这一轮不发。"""
        mentions = await self._mention_kinds.before_turn(
            workspace_id=conversation.workspace_id, user_id=conversation.user_id, conversation_id=conversation.id,
            answer_mode=turn.answer_mode, mentions=turn.mentions)
        return turn if mentions == turn.mentions else turn.model_copy(update={"mentions": mentions})

    async def _with_answer_mode(self, conversation: Conversation, turn: TurnInput) -> TurnInput:
        """扩展登记的回答方式：核对这个人能不能用，现算这一轮放行的官方工具与指令随消息带上（answer_modes.py）。
        内置的两种不带。不认识的抛 UnknownAnswerMode，指令算不出来抛 AnswerModeUnavailable，这一轮都不发。"""
        spec = await self._answer_modes.spec(turn.answer_mode, ModeContext(
            workspace_id=conversation.workspace_id, user_id=conversation.user_id, conversation_id=conversation.id))
        return turn if spec is None else turn.model_copy(update={"answer_mode_spec": spec})

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

    async def _with_context(self, conversation: Conversation, turn: TurnInput) -> TurnInput:
        """带上扩展补的上下文（turn_context.py）。没有扩展提供时不带；插话（steer）进的是正在跑的那一轮，不另算。"""
        if not self._turn_context or turn.mode != "followup":
            return turn
        context = await self._turn_context.collect(TurnContextRequest(
            workspace_id=conversation.workspace_id, user_id=conversation.user_id, conversation_id=conversation.id,
            text=turn.text, answer_mode=turn.answer_mode))
        return turn if context is None else turn.model_copy(update={"context": context})

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
        """本人的会话，最近有动静的在前；next 交回给 before 取下一页。后台会话（background.py）不列。"""
        limit = max(1, min(limit, MAX_PAGE))
        query = (select(Conversation)
                 .where(Conversation.workspace_id == workspace_id, Conversation.user_id == user_id,
                        Conversation.background.is_(False))
                 .order_by(Conversation.updated_at.desc(), Conversation.id.desc()).limit(limit + 1))
        if before is not None:
            query = query.where(Conversation.updated_at < before)
        async with self._sessionmaker() as session:
            rows = tuple((await session.execute(query)).scalars().all())
        page = rows[:limit]
        return ConversationPage(items=page, next=page[-1].updated_at if len(rows) > limit else None)

    async def list_by_ids(self, *, workspace_id: str, user_id: str, ids: tuple[str, ...]) -> tuple[Conversation, ...]:
        """本人的这几段对话，最近有动静的在前；别人的、后台的、不存在的不在里面。给侧栏按别的东西分组时一次取齐列表里还没翻到的
        （例如本机工作区里的对话），不一条一条地取。"""
        if not ids:
            return ()
        query = (select(Conversation)
                 .where(Conversation.workspace_id == workspace_id, Conversation.user_id == user_id,
                        Conversation.background.is_(False), Conversation.id.in_(ids))
                 .order_by(Conversation.updated_at.desc(), Conversation.id.desc()))
        async with self._sessionmaker() as session:
            return tuple((await session.execute(query)).scalars().all())

    async def search(self, *, workspace_id: str, user_id: str, query: str) -> tuple[SearchHit, ...]:
        """搜本人的对话：标题与每轮问的话（search.py）。"""
        return await search_conversations(self._sessionmaker, workspace_id=workspace_id, user_id=user_id, query=query)

    async def rename(self, conversation: Conversation, title: str) -> Conversation:
        clean = " ".join(title.split())[:200]
        await self._touch(conversation.id, title=clean)
        return await self.get(conversation_id=conversation.id, workspace_id=conversation.workspace_id,
                              user_id=conversation.user_id)

    async def delete(self, conversation: Conversation) -> None:
        """释放宿主会话、停泵、停止续期，再删事件、关联数据与会话本身。宿主上已不在也照删；在设备上执行的对话宿主上本来就没有，
        设备那边的本机会话由设备自己删。"""
        if not self._on_device(conversation):
            try:
                await self._host(conversation).dispose(conversation.id)
            except SessionNotFound:
                pass
        await self._pump.stop(conversation.id)
        if self._tool_credentials is not None:
            self._tool_credentials.forget(conversation.id)
        for erase in self._erasers:
            await erase(conversation)
        await self._scopes.release(conversation.workspace_id, conversation.id)  # 挂着的（例如专家）一起撤掉
        async with self._sessionmaker() as session:
            await session.execute(delete(RunEvent).where(RunEvent.conversation_id == conversation.id))
            await session.execute(delete(Conversation).where(Conversation.id == conversation.id))
            await session.commit()

    async def _follow(self, conversation: Conversation, profile: RuntimeProfile) -> None:
        """跟上本轮的配置：宿主相关部分变了（或还没绑定）才重开宿主会话；只是本轮选择变了只改绑定。"""
        bound = await self._profiles.bound(conversation)
        if bound is not None and bound.version == profile.version:
            return
        if bound is None or bound.host_version != profile.host_version:
            await self._reopen(conversation, profile)
        else:
            await self._bind(conversation, profile)

    async def _reopen(self, conversation: Conversation, profile: RuntimeProfile) -> None:
        """配置版本变了：释放宿主会话，以新策略恢复打开，再记下新版本。

        打开失败时不记新版本：下一轮还会再试；宿主上会话已不在，发送会走 SessionNotFound 的恢复路径。
        """
        log.info("runtime profile of %s changed to %s; reopening host session", conversation.id, profile.version[:12])
        try:
            await self._kernel.dispose(conversation.id)
        except SessionNotFound:
            pass
        await self._open_resumed(conversation, profile)
        self._opened(conversation)
        await self._bind(conversation, profile)

    async def _open_resumed(self, conversation: Conversation, profile: RuntimeProfile | None) -> None:
        """以恢复方式打开宿主会话。DSH 第一轮跑起来才写会话记录，一轮都没开始过的会话恢复不了
        （宿主日志里是 SessionPersistenceNotFoundError）：这时新开，宿主那边本来就没有东西可丢。
        新会话第一条消息就 @ 文件、选技能时配置版本会变、要重开，原来这里必然失败（2026-10-26）。
        跑过回合的会话打不开照旧报错，不悄悄新开、丢掉前面的上下文。"""
        try:
            await self._kernel.open_session(await self._spec(conversation, resume=True, profile=profile))
        except KernelError as error:
            if error.code != OPEN_FAILED or await self._has_turns(conversation.id):
                raise
            log.info("host has no record of %s yet (no turn has started); opening it fresh", conversation.id)
            await self._kernel.open_session(await self._spec(conversation, resume=False, profile=profile))

    async def _has_turns(self, conversation_id: str) -> bool:
        async with self._sessionmaker() as session:
            found = await session.scalar(select(RunEvent.seq).where(
                RunEvent.conversation_id == conversation_id, RunEvent.data["type"].astext == "turn/start").limit(1))
        return found is not None

    async def _resume_and_send(self, conversation: Conversation, turn: TurnInput,
                               profile: RuntimeProfile | None = None) -> str:
        return await self._resume_then(conversation, profile, lambda: self._kernel.send(conversation.id, turn))

    async def _resume_then(self, conversation: Conversation, profile: RuntimeProfile | None,
                           action: Callable[[], Awaitable[T]]) -> T:
        """宿主侧会话不在：以恢复方式打开再执行 action；宿主上仍找不到会话时按退避重试。"""
        for delay in (*self._resume_retry_delays, None):
            try:
                await self._open_resumed(conversation, profile)
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
        """打开事件流：确保泵在跑，再交出回放 + 实时的迭代器。在设备上执行的对话没有泵可拉，事件由设备送来时推过来。"""
        if not self._on_device(conversation):
            self._pump.ensure(conversation.id, conversation.workspace_id)
        return self._feed.iterate(conversation.id, cursor)

    async def cancel(self, conversation: Conversation) -> CancelResult:
        return await self._host(conversation).cancel(conversation.id)

    async def approvals(self, conversation: Conversation) -> list[PendingApproval]:
        """待审批的操作。在设备上执行的对话由设备应答，这里没有（卡片照样从事件里画出来）。"""
        if self._on_device(conversation):
            return []
        return await self._host(conversation).pending_approvals(conversation.id)

    async def decide(self, conversation: Conversation, approval_id: str, allow: bool, *,
                     remember: bool = False) -> None:
        """交回审批决定。remember 为真（批准并勾了「本会话内同类操作不再询问」）时先记授权，
        审批人就是对话所属用户（接口层已校验）；之后同一工具的审批由宿主应答者先查授权，不再弹卡片。"""
        host = self._host(conversation)
        if remember and self._grants is not None:
            pending = next((a for a in await host.pending_approvals(conversation.id) if a.id == approval_id), None)
            if pending is not None:
                await self._grants.remember(conversation.workspace_id, conversation.id, conversation.user_id,
                                            pending.tool_name)
        await host.decide_approval(conversation.id, approval_id, allow)

    async def questions(self, conversation: Conversation) -> list[PendingQuestion]:
        if self._on_device(conversation):
            return []
        return await self._host(conversation).pending_questions(conversation.id)

    async def answer(self, conversation: Conversation, question_id: str, answer: QuestionAnswer) -> None:
        """交回用户对一次提问的回答。与发送共用会话锁，宿主恢复会话时不会和发送撞上。"""
        host = self._host(conversation)
        async with self._lock(conversation.id):
            try:
                await host.answer_question(conversation.id, question_id, answer)
            except SessionNotFound:
                profile = await self._profile(conversation, NO_SELECTION)
                await self._resume_then(conversation, profile,
                                        lambda: host.answer_question(conversation.id, question_id, answer))
        await self._touch(conversation.id)
        self._pump.ensure(conversation.id, conversation.workspace_id)
