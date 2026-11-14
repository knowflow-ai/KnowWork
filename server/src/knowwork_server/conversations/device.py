"""在用户设备上执行的对话：智能体跑在原生应用的本机宿主上（例如桌面端在用户电脑上执行的对话），不在我们的宿主上。

它是一条普通对话（来源记 DEVICE_SOURCE）：列表、历史、事件流、改名、删除、按对话记的工具数据都照旧，所有端都从业务服务读。
不一样的只有两处：
- 事件由设备送进来：本机宿主的官方会话事件原样送到 push，经事件泵同一段编号、投影、落库、推给正在看的页面
  （pump.EventPump.push）；游标是官方事件的序号，重传的跳过，设备断了从 position 之后接着送。子 Agent 的事件在官方那边是
  另一个会话、序号另起：设备带上子会话编号送来，收成和我们宿主转发子 Agent 事件同一种帧（subagent/event），位置各记各的。
  设备只管原样送，怎么解读事件全在业务服务这边：以后界面要多认一种事件，只改这边，各端一起有；
- 每一轮由设备送达：发一句之前设备先来要这一轮的合同（prepare_turn），业务服务用和云端发消息同一套准备编出开会话与这一轮的
  请求体（kernel/wire.py，同我们宿主收的格式），设备原样交给它本机宿主里同一个收件逻辑；停止、审批、回答由设备交给自己的
  本机宿主，业务服务不接（service.RunsOnDevice）。审批卡片和云端对话一样取自会话日志里官方审批服务记的那一对
  （events_v1.APPROVAL_EVENTS）。审批由设备上宿主里的应答者答（和我们宿主上的同一个插件）；用户勾了「本会话内同类操作不再询问」
  时设备先来这里记授权（remember_approval，同云端按对话绑定的运行配置记），之后同一工具的审批由应答者经工具的公开入口查到、自动放行。

开源这边只有接口：谁能建、谁能送、设备是谁，由扩展判断（经 ApiContext.device_conversations 拿到它）。
"""

import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any

from ..approvals.grants import SessionGrants
from ..kernel.contract import HostModel, KernelEvent, TurnInput
from ..kernel.wire import models_body, session_body, turn_body
from ..profile.snapshot import NO_SELECTION, TurnSelection
from .constants import DEVICE_SOURCE
from .models import Conversation
from .pump import EventPump
from .scopes import ScopeClaim
from .service import ConversationNotFound, ConversationService

__all__ = ["DEVICE_EPOCH", "DEVICE_SOURCE", "MAX_EVENTS", "MAX_LIVE", "DeviceConversations", "DeviceTurnBodies",
           "InvalidEvents", "NotOnDevice", "Position"]

# 设备送来的帧都记在这一个来源下（run_event 的 host_epoch）：对话只在一台设备的一个会话里跑，官方序号本身单调递增
DEVICE_EPOCH = "device"
MAX_EVENTS = 500  # 一次最多送多少条，多的分几次送
MAX_TYPE = 64
MAX_LIVE = 500  # 一次最多送多少个逐字片段
# 子会话编号：来源记成「device:编号」，run_event.host_epoch 最长 64
SUB_SESSION = re.compile(r"^[A-Za-z0-9_.-]{1,56}$")
# 审批授权的工具名：同宿主审批应答者查授权的接口（tools/api.py 的 _GrantBody）
TOOL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class Position:
    """已收到哪：根会话的最后一个序号（一条都没有时 None），和各个子会话的。"""

    seq: int | None = None
    children: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class DeviceTurnBodies:
    """交给设备的这一轮：开会话的请求体（插话时没有）与这一轮的请求体，都是我们宿主收的格式。"""

    session: dict | None
    turn: dict


class NotOnDevice(ValueError):
    """这段对话不在设备上执行（设备那边的操作只认在设备上执行的对话）。"""


class InvalidEvents(ValueError):
    """送来的事件格式不对，或这段对话不在设备上执行。"""


def _frame(raw: Any, session: str | None) -> KernelEvent:
    if not isinstance(raw, dict):
        raise InvalidEvents("每条事件都要是对象")
    type_, seq, time = raw.get("type"), raw.get("seq"), raw.get("time", 0)
    if not isinstance(type_, str) or not 0 < len(type_) <= MAX_TYPE:
        raise InvalidEvents("事件缺类型")
    if type(seq) is not int or seq < 0 or type(time) is not int:
        raise InvalidEvents("事件的序号或时间不对")
    if session is None:
        return KernelEvent(epoch=DEVICE_EPOCH, cursor=seq, kind="session/event", time=time, data=raw)
    # 同宿主插件转发子 Agent 事件的帧（subagent-policy）：{sessionId, event}
    return KernelEvent(epoch=f"{DEVICE_EPOCH}:{session}", cursor=seq, kind="subagent/event", time=time,
                       data={"sessionId": session, "event": raw})


def frames_of(events: Sequence[Any], session: str | None = None) -> list[KernelEvent]:
    """校验并转成事件泵的帧：数量有上限，序号要从小到大（乱序的会被当成重传跳过，干脆不收）。"""
    if session is not None and not SUB_SESSION.fullmatch(session):
        raise InvalidEvents("子会话编号不对")
    if len(events) > MAX_EVENTS:
        raise InvalidEvents(f"一次最多送 {MAX_EVENTS} 条")
    frames = [_frame(raw, session) for raw in events]
    if any(later.cursor <= earlier.cursor for earlier, later in pairwise(frames)):
        raise InvalidEvents("事件要按序号从小到大")
    return frames


class DeviceConversations:
    def __init__(self, service: ConversationService, pump: EventPump, *,
                 models: Callable[[], Awaitable[tuple[HostModel, ...]]] | None = None,
                 grants: SessionGrants | None = None) -> None:
        self._service = service
        self._pump = pump
        self._models = models
        self._grants = grants

    async def models(self) -> dict:
        """可选对话模型的清单（同推给我们宿主的那份，kernel/wire.py 的 models_body）：设备上的本机宿主说不认识输入框选的模型时，
        设备来取、交给它，再发这一轮（同云端「宿主说不认识就重推清单再发」）。"""
        return models_body(await self._models() if self._models is not None else ())

    async def create(self, *, workspace_id: str, user_id: str, title: str = "",
                     scope: ScopeClaim | None = None) -> Conversation:
        """建一条在设备上执行的对话（不开宿主会话）。设备随后用它的编号建本机会话。scope：要挂的东西（例如专家），
        同云端建对话，挂不上抛 ScopeRejected。"""
        return await self._service.create_on_device(workspace_id=workspace_id, user_id=user_id, title=title,
                                                    scope=scope)

    async def delete(self, conversation: Conversation) -> None:
        """删掉这段对话（连同已收到的事件与按对话记的数据）。扩展建完对话、后面的步骤没成时用它收回。"""
        await self._service.delete(conversation)

    async def owned(self, *, workspace_id: str, user_id: str, conversation_id: str) -> Conversation | None:
        """这个人名下、在设备上执行的那段对话；别人的、在服务端执行的、不存在的都是 None。"""
        try:
            conversation = await self._service.get(conversation_id=conversation_id, workspace_id=workspace_id,
                                                   user_id=user_id)
        except ConversationNotFound:
            return None
        return conversation if conversation.source == DEVICE_SOURCE else None

    async def session(self, conversation: Conversation) -> dict:
        """开会话的合同（同我们宿主收的请求体，kernel/wire.py）：设备建好对话后交给本机宿主的收件逻辑开会话。"""
        self._on_device(conversation)
        return session_body(await self._service.device_session(conversation))

    async def prepare_turn(self, conversation: Conversation, turn: TurnInput,
                           selection: TurnSelection = NO_SELECTION) -> DeviceTurnBodies:
        """设备发一轮之前：和云端发消息同一套准备（service.prepare_on_device），返回同我们宿主收的两份请求体，设备原样交给
        本机宿主的收件逻辑——先按开会话的那份对齐会话，再发这一轮。核对不过抛的错同云端发消息。"""
        self._on_device(conversation)
        prepared = await self._service.prepare_on_device(conversation, turn, selection)
        return DeviceTurnBodies(session=None if prepared.session is None else session_body(prepared.session),
                                turn=turn_body(prepared.turn))

    @staticmethod
    def _on_device(conversation: Conversation) -> None:
        if conversation.source != DEVICE_SOURCE:
            raise NotOnDevice(conversation.id)

    async def remember_approval(self, conversation: Conversation, tool_name: str) -> bool:
        """记「本会话内同类操作不再询问」（同云端 ConversationService.decide 里记的那一条）：按这段对话此刻绑定的运行配置记，
        之后设备上的审批应答者经工具的公开入口查到就自动放行。对话还没绑定运行配置时不记，返回 False（下次照常问）。"""
        self._on_device(conversation)
        if not TOOL_NAME.fullmatch(tool_name):
            raise ValueError("工具名不对")
        if self._grants is None:
            return False
        return await self._grants.remember(conversation.workspace_id, conversation.id, conversation.user_id, tool_name)

    async def push(self, conversation: Conversation, events: Sequence[Any], *, session: str | None = None) -> int | None:
        """收下设备送来的一批官方会话事件，返回这个会话已收到的最后一个序号。session 是子 Agent 的会话编号，不给是根会话。"""
        if conversation.source != DEVICE_SOURCE:
            raise InvalidEvents("这段对话不在设备上执行")
        return await self._pump.push(conversation.id, conversation.workspace_id, frames_of(events, session))

    async def push_live(self, conversation: Conversation, frames: Sequence[Any]) -> None:
        """收下设备送来的逐字片段：模型正在写的正文、思考，只推给正在看的页面，不落库（正式的消息随后作为事件送来）。
        片段是宿主那种临时帧（设备用和宿主同一个函数整理官方的流式帧），认不出的由 live.advance 丢掉。"""
        if conversation.source != DEVICE_SOURCE:
            raise InvalidEvents("这段对话不在设备上执行")
        if len(frames) > MAX_LIVE or not all(isinstance(frame, dict) for frame in frames):
            raise InvalidEvents("逐字片段格式不对或太多")
        await self._pump.push_live(conversation.id, conversation.workspace_id, frames)

    async def position(self, conversation: Conversation) -> Position:
        """已收到哪（设备从这之后接着送）。"""
        cursors = await self._pump.pushed_upto(conversation.id, conversation.workspace_id)
        prefix = f"{DEVICE_EPOCH}:"
        return Position(seq=cursors.get(DEVICE_EPOCH),
                        children={epoch[len(prefix):]: cursor for epoch, cursor in cursors.items()
                                  if epoch.startswith(prefix)})
