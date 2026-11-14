"""用量配额的判定口子：开始一轮之前问一句「这个人现在还能不能用」。

各端开始一轮都经过这里：网页发消息与网页嵌入、桌面端本机会话开始一轮、IM 机器人、后台对话（都经 ConversationService 的
同一段准备），以及宿主上的定时任务到点激活对话。只判开始，不打断进行中的一轮。

开源版不限制：没有扩展登记判定时什么都不调，和没有这个功能时一样。额度怎么定、怎么算、超了说什么都在扩展里。
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from typing import Literal

Trigger = Literal["user", "schedule"]
Scope = Literal["personal", "workspace"]


@dataclass(frozen=True)
class TurnStart:
    """要开始的这一轮：在哪个空间、算谁的、哪段对话，用户发的还是定时任务到点。user_id 是对话所属账号，渠道里没认出人时
    是渠道账号（扩展要分真人可以查 profiles 的 kind）。"""

    workspace_id: str
    user_id: str
    conversation_id: str
    trigger: Trigger = "user"


class QuotaExceeded(Exception):
    """额度用完了，这一轮不开始。message 是给用户看的话（扩展写，说清楚为什么、哪天恢复、找谁）；scope 说是个人额度
    还是空间总额用完了；resets_on 是哪天恢复（不知道是 None）。"""

    def __init__(self, message: str, *, scope: Scope, resets_on: date | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.scope = scope
        self.resets_on = resets_on

    def detail(self) -> dict[str, str | None]:
        """接口错误的 detail：前端照常显示 message，按 code 换成输入框上的提示。"""
        return {"code": "quota_exceeded", "message": self.message, "scope": self.scope,
                "resets_on": None if self.resets_on is None else self.resets_on.isoformat()}


QuotaCheck = Callable[[TurnStart], Awaitable[None]]  # 能开始就返回；不能抛 QuotaExceeded


class QuotaPolicies:
    """扩展在业务服务启动时登记判定（只能一个：两份额度规则叠在一起说不清以哪个为准），之后只读。"""

    def __init__(self) -> None:
        self._check: QuotaCheck | None = None

    def set(self, check: QuotaCheck) -> None:
        if self._check is not None:
            raise ValueError("已经登记过配额判定")
        self._check = check

    def __bool__(self) -> bool:
        return self._check is not None

    async def before_turn(self, start: TurnStart) -> None:
        """开始一轮之前调；超了抛 QuotaExceeded。判定自己出错（例如数据库一时连不上）也照样抛出去：这一轮不发，
        调用方按「暂时不可用」说，不悄悄放过（放过就等于没有配额）。"""
        if self._check is not None:
            await self._check(start)
