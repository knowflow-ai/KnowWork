"""回答方式登记表：输入框左上角那个下拉里的几项。内置智能体与快速问答；扩展可以再登记（业务服务启动时，之后只读）。

一种回答方式管两件事：这一轮模型能用哪些官方全局工具、提问后面多一段什么指令（宿主 answer-mode 照着收窄与注入）。
业务工具在哪几种回答方式里提供，由定义工具的模块在 ToolSpec.answer_modes 里自己声明，不在这里。
- 内置的两种宿主自己认得，发消息时不带额外内容，行为与原来一样；
- 扩展登记的方式发消息时现算（指令可能按会话变，例如带上会话范围的摘要），随消息交给宿主；
- available 决定这个空间、这个人能不能选（下拉里出不出现、发消息时认不认），判断出错时按不能选；
- listed 为假的不进下拉，只在扩展自己的页面里用（例如扩展页面右栏里的对话），或给扩展的后台对话用（conversations/background.py，
  例如在 worker 里起草，没人在旁边点审批，这种方式只放只读的工具、把结果写在回答里交回去）。发消息时照样认；用它开的对话
  也不进本人的「最近」和搜索（service.create）：在聊天页里打开它接着发，下拉里没有这种方式，会换成别的方式答；
- channel 写了才能在 IM 渠道里选（channels/settings.py）：渠道不认人或在群里时用机器人账号回答，按这一类资源给它授权，
  内置两种查知识库。没写的（例如改模型的那种，IM 里没法审阅）不进渠道。
"""

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ..kernel.contract import ANSWER_MODE_PATTERN, AnswerModeSpec

log = logging.getLogger(__name__)

MODE_ID = re.compile(ANSWER_MODE_PATTERN)
TOOL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
RESOURCE_TYPE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")  # 同权限词汇的资源类型（access/vocab.py TYPE_PATTERN）
MAX_PROMPT = 20_000
MAX_LABEL = 20
MAX_DESCRIPTION = 60


@dataclass(frozen=True)
class ModeContext:
    workspace_id: str
    user_id: str
    conversation_id: str | None = None  # 列下拉时没有会话


@dataclass(frozen=True)
class ChannelResource:
    """IM 渠道里这种回答方式查哪类资源：授权里的资源类型与它的名字（机器人账号按它授权，界面上写「群聊可查的<名字>」）。"""

    type: str
    label: str


KNOWLEDGE = ChannelResource("knowledge_base", "知识库")


@dataclass(frozen=True)
class AnswerMode:
    id: str
    label: str  # 下拉里的名字
    description: str  # 下拉里的一句说明
    official_tools: tuple[str, ...] = ()  # 放行的官方全局工具（ask_user_question、present、todo_write、skill……）
    prompt: Callable[[ModeContext], Awaitable[str]] | None = None
    available: Callable[[ModeContext], Awaitable[bool]] | None = None
    listed: bool = True  # 进不进下拉；只在扩展页面里或后台对话用的设 False，用它开的对话也不进「最近」
    channel: ChannelResource | None = None  # IM 渠道里能不能选、机器人账号按哪类资源授权


AGENT = AnswerMode("agent", "智能体", "能用技能、读文件、执行任务，适合复杂的事", channel=KNOWLEDGE)
QUICK = AnswerMode("quick", "快速问答", "只查知识库，回答更快", channel=KNOWLEDGE)
BUILT_IN = (AGENT, QUICK)


class UnknownAnswerMode(Exception):
    """没登记、或这个人此刻不能用的回答方式。"""


class AnswerModeUnavailable(Exception):
    """登记了也能用，但这一轮的指令没算出来（扩展出错）：宁可这一轮发不出去，也不让模型少了规则答题。"""


class AnswerModes:
    def __init__(self) -> None:
        self._items: dict[str, AnswerMode] = {mode.id: mode for mode in BUILT_IN}

    def add(self, mode: AnswerMode) -> None:
        if not MODE_ID.fullmatch(mode.id):
            raise ValueError(f"invalid answer mode id: {mode.id!r}")
        if mode.id in self._items:
            raise ValueError(f"answer mode already registered: {mode.id}")
        if not 0 < len(mode.label) <= MAX_LABEL or len(mode.description) > MAX_DESCRIPTION:
            raise ValueError(f"answer mode {mode.id} needs a short label and description")
        if not all(TOOL_NAME.fullmatch(name) for name in mode.official_tools):
            raise ValueError(f"answer mode {mode.id} has an invalid tool name")
        if mode.channel is not None and (not RESOURCE_TYPE.fullmatch(mode.channel.type) or not mode.channel.label):
            raise ValueError(f"answer mode {mode.id} has an invalid channel resource")
        self._items = {**self._items, mode.id: mode}

    async def for_user(self, ctx: ModeContext) -> list[AnswerMode]:
        return [mode for mode in self._items.values() if mode.listed and await _offered(mode, ctx)]

    def listed(self, mode_id: str) -> bool:
        """这种回答方式进不进下拉（没登记的按进）：不进的开出来的对话也不进「最近」和搜索。"""
        mode = self._items.get(mode_id)
        return mode is None or mode.listed

    async def offered(self, mode_id: str, ctx: ModeContext) -> bool:
        """这个人此刻能不能选这种回答方式（下拉里有没有它）。"""
        mode = self._items.get(mode_id)
        return mode is not None and mode.listed and await _offered(mode, ctx)

    async def for_channels(self, ctx: ModeContext) -> list[AnswerMode]:
        """IM 渠道能选的：这个人（配渠道的管理员）能用、又写了查哪类资源的。"""
        return [mode for mode in await self.for_user(ctx) if mode.channel is not None]

    def channel_resource(self, mode_id: str) -> ChannelResource | None:
        mode = self._items.get(mode_id)
        return mode.channel if mode is not None else None

    def channel_types(self) -> frozenset[str]:
        """渠道里会用到的全部资源类型：机器人账号在这几类上的授权由渠道配置管。"""
        return frozenset(mode.channel.type for mode in self._items.values() if mode.channel is not None)

    async def spec(self, mode_id: str, ctx: ModeContext) -> AnswerModeSpec | None:
        """这一轮随消息带给宿主的内容；内置的两种不带。"""
        if mode_id in (AGENT.id, QUICK.id):
            return None
        mode = self._items.get(mode_id)
        if mode is None or not await _offered(mode, ctx):
            raise UnknownAnswerMode(mode_id)
        try:
            prompt = await mode.prompt(ctx) if mode.prompt is not None else ""
        except Exception as error:
            log.exception("answer mode %s prompt failed for conversation %s", mode_id, ctx.conversation_id)
            raise AnswerModeUnavailable(mode_id) from error
        return AnswerModeSpec(tools=tuple(sorted(set(mode.official_tools))), prompt=prompt[:MAX_PROMPT])


async def _offered(mode: AnswerMode, ctx: ModeContext) -> bool:
    if mode.available is None:
        return True
    try:
        return await mode.available(ctx)
    except Exception:
        log.exception("availability of answer mode %s failed for workspace %s", mode.id, ctx.workspace_id)
        return False
