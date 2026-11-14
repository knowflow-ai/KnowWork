"""渠道的统一处理：平台适配器把消息交上来，这里走完

去重 → 限流 → 命令 → 找或建会话 → 发一轮 → 跟着会话事件流式回发。

不认人的渠道：私聊、群聊都用渠道账号（和渠道同 id 的 user.kind = channel）回答，能查的就是授给它的知识库
（回答方式查别的资源时是那一类，例如问数的数据源）。
认人的渠道（people.py）：私聊按提问人本人回答，会话记在本人名下，这一轮要等人确认或跟太久时不取消、给网页链接；
群聊仍用渠道账号（回答全群看得见）。智能体追问时在 IM 里问（questions.py），这个对话位置的下一条消息就是回答。会话、判权、知识检索全走 ConversationService，和网页同一套；每个对话位置
（私聊的人、群里的人）一个会话，记下提问人在平台上的名字给管理员看。同一个对话位置一次只答一条，后来的排队，
免得两轮的事件搅在一起。
"""

import asyncio
import logging
import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Protocol

from ..conversations.answer_modes import KNOWLEDGE, AnswerModes, ChannelResource, ModeContext, UnknownAnswerMode
from ..conversations.quota import QuotaExceeded
from ..conversations.replay import LIVE_TYPE, Cursor, Outgoing
from ..conversations.service import ConversationNotFound
from ..db.base import in_workspace
from ..kernel.contract import KernelError, PendingQuestion, TurnInput
from ..profile.snapshot import TurnSelection
from . import progress, questions
from .cards import has_details, render
from .people import MEMBER, NOT_MEMBER, People
from .pictures import Pictures
from .render import clean, clip
from .settings import ChannelSettings, read_settings
from .store import ChannelStore
from .types import Connection, IncomingMessage, ReplyStream

log = logging.getLogger(__name__)

THINKING = "正在思考…"
ANSWER_TIMEOUT = 15 * 60  # 一轮最多跟这么久，再长就停下
PUSH_INTERVAL = 1.0  # 流式内容最多一秒推一次，平台对同一条消息的更新频率有限制
DEDUP_SECONDS = 10 * 60
RATE_WINDOW, RATE_LIMIT = 60, 20  # 每人每分钟最多 20 条
MAX_TEXT = 4000

NEW_COMMANDS = {"/new", "新会话", "新对话"}
HELP_COMMANDS = {"/help", "帮助"}
HELP = ("直接提问就行，我会接着之前的对话回答。\n"
        "- 发「新会话」另起一个对话\n"
        "- 我问你问题时，回复选项前的序号，或者直接写你的说法")
NO_BASES = "这个机器人还没选可查的{label}，请联系管理员。"
NO_GROUP_BASES = "这个机器人在群里没有可查的{label}。要按你自己的权限查，请私聊我。"
NO_ACCESS = "你还没有可查的{label}，请联系管理员授权。"
NOT_READY = "这个机器人的配置不完整，请联系管理员在渠道里重新保存一次。"
WAITING = {"approval": "这一步要你确认", "question": "这一步要你补充信息"}
STILL_WAITING = "上一步还在等你确认，先在网页里处理完再接着问：{link}"


class Conversations(Protocol):
    """ConversationService 里渠道用到的部分。"""

    async def create(self, *, workspace_id: str, user_id: str, title: str = "",
                     selection: TurnSelection = ..., channel_id: str | None = None, source: str = ""): ...

    async def get(self, *, conversation_id: str, workspace_id: str, user_id: str): ...

    async def send(self, conversation, turn: TurnInput, selection: TurnSelection = ...) -> str: ...

    def events(self, conversation, cursor: Cursor) -> AsyncIterator[Outgoing]: ...

    async def cancel(self, conversation): ...

    async def questions(self, conversation) -> list[PendingQuestion]: ...

    async def approvals(self, conversation) -> list: ...

    async def answer(self, conversation, question_id: str, answer) -> None: ...


MemberCheck = Callable[[str, str], bool]  # (用户, 空间) → 是不是空间成员


@dataclass
class _Window:
    """去重与限流都只在本进程内存里记：业务服务单进程，重启后最多多答一次。"""

    seen: dict[str, float] = field(default_factory=dict)
    hits: dict[str, deque] = field(default_factory=dict)

    def duplicate(self, key: str, now: float) -> bool:
        if len(self.seen) > 10_000:
            self.seen = {k: t for k, t in self.seen.items() if now - t < DEDUP_SECONDS}
        if key in self.seen and now - self.seen[key] < DEDUP_SECONDS:
            return True
        self.seen[key] = now
        return False

    def allow(self, key: str, now: float) -> bool:
        hits = self.hits.setdefault(key, deque())
        while hits and now - hits[0] > RATE_WINDOW:
            hits.popleft()
        if len(hits) >= RATE_LIMIT:
            return False
        hits.append(now)
        return True


class ChannelService:
    def __init__(self, store: ChannelStore, conversations: Conversations, *, member: MemberCheck,
                 people: People | None = None, pictures: Pictures | None = None, modes: AnswerModes | None = None,
                 answer_timeout: float = ANSWER_TIMEOUT, push_interval: float = PUSH_INTERVAL) -> None:
        self._store = store
        self._conversations = conversations
        self._member = member
        self._people = people
        self._modes = modes or AnswerModes()
        self._pictures = pictures
        self._answer_timeout = answer_timeout
        self._push_interval = push_interval
        self._window = _Window()
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    async def handle(self, message: IncomingMessage, connection: Connection) -> None:
        """平台适配器收到消息时调用。出错只记日志、回一句，不往上抛（不能把连接打断）。"""
        with in_workspace(message.workspace_id):
            try:
                await self._handle(message, connection)
            except Exception:
                log.exception("channel %s failed to handle message %s", message.channel_id, message.message_id)
                await _quietly(connection.reply(message, "处理这条消息时出错了，请稍后再试。"))

    async def _handle(self, message: IncomingMessage, connection: Connection) -> None:
        now = time.monotonic()
        if message.message_id and self._window.duplicate(f"{message.channel_id}:{message.message_id}", now):
            return  # 平台重投的同一条
        if not self._window.allow(f"{message.channel_id}:{message.external_id}", now):
            await connection.reply(message, "你发得太快了，请稍后再试。")
            return
        channel = await self._store.get(message.workspace_id, message.channel_id)
        if channel is None or not channel.enabled:
            return
        text = message.text.strip()[:MAX_TEXT]
        if not text:
            await connection.reply(message, "目前只能回答文字提问。")
            return
        if text.lower() in NEW_COMMANDS:
            await self._store.forget(message.workspace_id, message.channel_id, message.session_key)
            await connection.reply(message, "已开始新会话，接下来的提问不再接着之前的对话。")
            return
        if text.lower() in HELP_COMMANDS:
            await connection.reply(message, HELP)
            return
        settings = read_settings(channel.settings)
        resource = self._modes.channel_resource(settings.answer_mode) or KNOWLEDGE
        if channel.audience == MEMBER and message.chat_type == "direct":
            person = await self._recognize(channel, settings, message, connection)
            if person is None:
                return
            if not await self._usable(settings, message.workspace_id, person):
                # 例如问数：他一个数据源都没有。先说清楚再建会话，不在他的「最近」里留一个空对话
                await connection.reply(message, NO_ACCESS.format(label=resource.label))
                return
            async with self._lock(message.channel_id, message.session_key):
                await self._answer(message, connection, settings, person, text, resource, personal=True)
            return
        if not (settings.bases or settings.resources):
            template = NO_GROUP_BASES if channel.audience == MEMBER else NO_BASES
            await connection.reply(message, template.format(label=resource.label))
            return
        account = channel.id  # 渠道账号和渠道同 id
        if not self._member(account, message.workspace_id) or not await self._usable(settings, message.workspace_id,
                                                                                        account):
            await connection.reply(message, NOT_READY)  # 渠道账号被人从空间里移出去了，或回答方式没了（扩展卸了）
            return
        async with self._lock(message.channel_id, message.session_key):
            await self._answer(message, connection, settings, account, text, resource)

    async def _usable(self, settings: ChannelSettings, workspace_id: str, user_id: str) -> bool:
        return await self._modes.offered(settings.answer_mode, ModeContext(workspace_id, user_id))

    async def _recognize(self, channel, settings: ChannelSettings, message: IncomingMessage,
                         connection: Connection) -> str | None:
        """认人的渠道里私聊：对上而且是空间成员就返回本人；否则回一句原因、记进没认出的人。"""
        source = await self._people.source(settings.identity_source) if self._people is not None else None
        if source is None:  # 来源被删了，或没有能认人的扩展
            await connection.reply(message, NOT_READY)
            return None
        found = await self._people.resolve(source, message)
        reason = found.reason
        if found.user_id is not None and not self._member(found.user_id, message.workspace_id):
            reason = NOT_MEMBER
        if reason:
            await self._store.stranger(message.workspace_id, message.channel_id, message.external_id,
                                       message.display_name, reason)
            await connection.reply(message, self._people.stranger_reply(reason, source))
            return None
        await self._store.recognized(message.workspace_id, message.channel_id, message.external_id)
        return found.user_id

    def _lock(self, channel_id: str, key: str) -> asyncio.Lock:
        if len(self._locks) > 5_000:
            self._locks = {k: lock for k, lock in self._locks.items() if lock.locked()}
        return self._locks.setdefault((channel_id, key), asyncio.Lock())

    async def _answer(self, message: IncomingMessage, connection: Connection, settings: ChannelSettings,
                      user_id: str, text: str, resource: ChannelResource, *, personal: bool = False) -> None:
        # 不点名：能查的全部。渠道账号是授给它的那几个知识库（或数据源这类）；认出的人是他自己能查的
        selection = TurnSelection()
        stream = await connection.stream(message, THINKING)
        try:
            conversation, fresh = await self._conversation(message, user_id, text, selection)
            link = self._people.link(f"/chat/{conversation.id}") if personal and self._people is not None else None
            if link and not fresh and await self._awaiting_approval(conversation):
                await stream.finish(STILL_WAITING.format(link=link))
                return
            cursor = Cursor(await self._store.last_seq(conversation.id))
            resume = not fresh and await self._reply(conversation, text)
            if not resume:
                await self._conversations.send(conversation, TurnInput(text=text, answer_mode=settings.answer_mode),
                                               selection)
        except UnknownAnswerMode:  # 刚核对过能用，发的时候又不能用了（授权刚被收回）
            await stream.finish(NO_ACCESS.format(label=resource.label) if personal else NOT_READY)
            return
        except QuotaExceeded as error:  # 额度用完：说清楚为什么、哪天恢复（和网页上同一句）
            await stream.finish(error.message)
            return
        except Exception:
            log.exception("channel %s could not start a turn", message.channel_id)
            await stream.finish("现在没法回答，请稍后再试。")
            return
        answer, raw = await self._follow(conversation, cursor, stream, link, resume=resume)
        pictures = []
        if self._pictures is not None:
            try:
                pictures = await self._pictures.of(message.workspace_id, conversation.id, user_id, raw)
            except Exception:
                log.warning("channel %s could not collect pictures", message.channel_id, exc_info=True)
        await stream.finish(clip(answer), pictures)
        await self._store.touch(message.workspace_id, message.channel_id, message.session_key,
                                message.display_name)

    async def _conversation(self, message: IncomingMessage, user_id: str, text: str,
                            selection: TurnSelection) -> tuple[object, bool]:
        """这个对话位置的会话，和它是不是刚建的（刚建的不会有等着回答的提问）。"""
        existing = await self._store.conversation_of(message.workspace_id, message.channel_id,
                                                     message.session_key, user_id)
        if existing is not None:
            try:
                return await self._conversations.get(conversation_id=existing, workspace_id=message.workspace_id,
                                                     user_id=user_id), False
            except ConversationNotFound:
                pass  # 会话在网页里删掉了：另起一个
        conversation = await self._conversations.create(workspace_id=message.workspace_id, user_id=user_id,
                                                        title=text[:40], selection=selection,
                                                        channel_id=message.channel_id, source=message.platform)
        await self._store.remember(message.workspace_id, message.channel_id, message.session_key,
                                   conversation.id, user_id, message.display_name or message.external_id)
        return conversation, True

    async def _reply(self, conversation, text: str) -> bool:
        """会话里有等着回答的提问时，这条消息就是回答：交回去返回真。没有、或问题已经结束（到期、在网页里答过了）
        返回假，当新的提问发。"""
        pending = await self._pending(conversation)
        if pending is None:
            return False
        try:
            await self._conversations.answer(conversation, pending.id, questions.reply(pending, text))
        except KernelError as error:
            if error.code == "question_not_found":
                return False
            raise
        return True

    async def _awaiting_approval(self, conversation) -> bool:
        """认出的人的会话上一轮停在等批准（IM 里点不了，给过网页链接）：这时再发会排在那一轮后面一直等。
        取不到（宿主那边没有这个会话）当没有。"""
        try:
            return bool(await self._conversations.approvals(conversation))
        except Exception:
            log.warning("channel could not list approvals of %s", conversation.id, exc_info=True)
            return False

    async def _pending(self, conversation, question_id: str = "") -> PendingQuestion | None:
        """等着回答的那次提问：按 id 找，找不到取还在前台等的、再取第一个。取不到（宿主那边没有这个会话）当没有。"""
        try:
            found = await self._conversations.questions(conversation)
        except Exception:
            log.warning("channel could not list questions of %s", conversation.id, exc_info=True)
            return None
        return (next((q for q in found if q.id == question_id), None)
                or next((q for q in found if q.state == "open"), None) or (found[0] if found else None))

    async def _follow(self, conversation, cursor: Cursor, stream: ReplyStream,
                      link: str | None = None, *, resume: bool = False) -> tuple[str, str]:
        """跟着这一轮的事件：逐字片段推成流式内容，答完返回（给人看的回答，回答原文）。原文里带着放图标记，
        答完按它取图。只认发出这一轮之后的第一个 run.started；resume 是交回追问的回答以后接着跟那一轮（没有新的
        run.started，问题到期过的会起新的一轮）。

        追问在 IM 里问，不停下这一轮，等下一条消息（_reply）。IM 里没法点批准：用渠道账号答的，等批准时直接停下这一轮，
        跟太久也停下，不让它在后台一直跑；认出的人（给了 link）会话在本人名下，不停，给网页链接让他去网页里接着确认、
        接着看。"""
        follower = _Follower(resume=resume)
        last_push = 0.0
        try:
            async with asyncio.timeout(self._answer_timeout):
                async for item in self._conversations.events(conversation, cursor):
                    done = follower.take(item)
                    if done is not None:
                        asked = await self._pending(conversation, follower.waiting_id) \
                            if follower.stuck and follower.waiting == "question" else None
                        if asked is not None:
                            shown = _attached(follower.answer, follower.cards, None)
                            return "\n\n".join(p for p in (shown, questions.render(asked)) if p), follower.answer_raw
                        if follower.stuck and link:
                            why = WAITING.get(follower.waiting, WAITING["question"])
                            shown = _attached(follower.answer, follower.cards, None)
                            return f"{shown}\n\n（{why}，点这里在网页里继续：{link}）".strip(), ""
                        if follower.stuck:
                            await _quietly(self._conversations.cancel(conversation))
                        return _attached(done, follower.cards, link), follower.answer_raw
                    shown = follower.shown()
                    if shown and time.monotonic() - last_push >= self._push_interval:
                        last_push = time.monotonic()
                        await _quietly(stream.update(shown))
        except TimeoutError:
            if link:
                return f"{follower.answer}\n\n（还在回答，点这里在网页里看：{link}）".strip(), ""
            await _quietly(self._conversations.cancel(conversation))
            return f"{follower.answer}\n\n（回答时间太长，已停下，可以把问题拆小一点再问）".strip(), ""
        return follower.answer or "这一轮没有生成回答。", follower.answer_raw


@dataclass
class _Follower:
    """把一轮的事件变成 IM 里那一条消息的内容。

    一步说完的话要看它后面有没有调工具，才知道是过渡说明还是最终回答：调了工具就收进上方的进度块（只追加不替换），
    没调工具、这一轮就结束的就是回答。所以过程中显示「进度块 + 当前这一步的文字」，答完整条换成只有回答。"""

    run_id: str | None = None
    live: str = ""  # 当前这一步正在出的字
    pending: str = ""  # 当前这一步说完的话，还不知道是过渡说明还是回答
    pending_raw: str = ""  # 同上，没去掉引用与放图标记的原文
    last_message: str = ""  # 最近一条说完的话（没有回答时兜底用）
    last_raw: str = ""
    steps: list[str] = field(default_factory=list)
    cards: list[dict] = field(default_factory=list)  # 工具交给界面的卡片（tools/presentation.py），答完转成文字附上
    stuck: bool = False  # 这一轮停在等人（批准、追问）
    waiting: str = ""  # 等的是什么：approval / question
    waiting_id: str = ""  # 等的那次提问或审批的 id
    resume: bool = False  # 交回追问的回答后接着跟：同一轮接着跑没有 run.started，认第一个带轮次的事件

    def take(self, item: Outgoing) -> str | None:
        """吃进一个事件；这一轮结束时返回要回给用户的文字。"""
        payload = item.payload if isinstance(item.payload, dict) else {}
        if item.type == "run.started" and (self.run_id is None or self.resume):
            self.run_id, self.resume = payload.get("run_id"), False  # 问题到期过的，回答起的是新的一轮
            return None
        if self.run_id is None and self.resume:
            self.run_id = payload.get("run_id")
        if self.run_id is None or payload.get("run_id") != self.run_id:
            return None  # 上一轮剩下的，或别的轮次
        if item.type == LIVE_TYPE:
            self._live(payload)
        elif item.type == "item.completed" and payload.get("kind") == "message":
            data = payload.get("data") or {}
            if data.get("role") == "assistant" and data.get("text"):
                self.pending_raw = self.last_raw = str(data["text"])
                self.pending = self.last_message = clean(self.pending_raw)
                self.live = ""
        elif item.type == "item.completed" and payload.get("kind") == "tool":
            presentation = (payload.get("data") or {}).get("presentation")
            if isinstance(presentation, dict) and isinstance(presentation.get("cards"), list):
                self.cards.extend(card for card in presentation["cards"] if isinstance(card, dict))
        elif item.type == "item.started" and payload.get("kind") == "tool":
            data = payload.get("data") or {}
            # 调了工具：这一步说的话是过渡说明，收进进度；没说话的就记工具在做什么
            line = progress.one_line(self.pending) if self.pending else \
                progress.tool_line(str(data.get("name") or ""), data.get("arguments"))
            self.steps.append(line)
            self.pending, self.pending_raw, self.live = "", "", ""
        elif item.type == "run.waiting":
            self.stuck = True
            self.waiting_id = str(payload.get("item_id") or "")
            reason = (payload.get("data") or {}).get("reason")
            self.waiting = "approval" if reason == "approval" else "question"
            why = "需要人工确认才能继续，机器人这里做不了" if reason == "approval" else \
                "需要你补充信息，可以把问题说得更具体些再发一次"
            return f"{self.answer}\n\n（{why}）".strip()
        elif item.type == "run.completed":
            return self.answer or "这一轮没有生成回答。"
        elif item.type == "run.failed":
            reason = str((payload.get("data") or {}).get("message") or "执行失败")
            return f"{self.answer}\n\n（这一轮没有完成：{reason}）".strip()
        elif item.type == "run.cancelled":
            return f"{self.answer}\n\n（已停止）".strip()
        return None

    @property
    def answer(self) -> str:
        """最后要留下的回答：最后一步没调工具时说的话；最后一步也调了工具（没再说话就结束了）时用最近说的那句。"""
        return self.pending or self.last_message

    @property
    def answer_raw(self) -> str:
        return self.pending_raw or self.last_raw

    def _live(self, payload: dict) -> None:
        if payload.get("done"):
            return
        text, offset = str(payload.get("text") or ""), payload.get("offset")
        if offset == 0:
            self.live = text
        elif offset == len(self.live):
            self.live += text
        # 对不上说明中间漏了，等这一步的正式消息整段替换

    def shown(self) -> str:
        """过程中的样子：上方进度块，下方当前这一步的文字。"""
        current = clean(self.live) or self.pending
        return "\n\n".join(part for part in (progress.render(self.steps), current) if part)


def _attached(text: str, cards: list[dict], link: str | None) -> str:
    """回答后面附上卡片的文字（cards.py）；卡片里有 IM 只给了一部分的（图、出处明细），认出的人再给网页链接。"""
    parts = [text, render(cards)]
    if link and has_details(cards):
        parts.append(f"（完整的结果、图表和出处在网页里看：{link}）")
    return "\n\n".join(part for part in parts if part)


async def _quietly(awaitable) -> None:
    try:
        await awaitable
    except Exception:
        log.warning("channel reply failed", exc_info=True)
