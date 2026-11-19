"""扩展随每一轮交给宿主插件的状态（kernel/contract.TurnInput.extension_state）：扩展在业务服务启动时按名字登记提供者，之后只读。

和补上下文（turn_context.py）的分别：那是给模型看的文字，这是给扩展自己的宿主插件用的数据（例如 Office 编辑：这段对话此刻在
编辑器里开着哪几份，插件据此决定给不给模型编辑器工具）。提供者也可以在交出状态之前做这一轮开始前该做的事（Office 编辑先把
编辑器里没存的改动存盘、存成新的一版，模型这一轮读到的是用户最新的），所以每轮只问一次、在发出之前。

- 每个提供者交回一份 JSON（交回 None 就不带这一项），按名字放进这一轮的 extension_state；
- 一个提供者出错或超时只少它那一项，不连累别的（同 turn_context 的降级：能降级的提供者自己接住）；
- 插话（steer）进的是正在跑的那一轮，不另算；没登记时这一轮不带（宿主沿用上一轮的）。
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from ..kernel.contract import EXTENSION_NAME_PATTERN

log = logging.getLogger(__name__)

TIMEOUT = 10.0  # 存盘、取回要些时间；超了这一项不带，宿主沿用上一轮的


@dataclass(frozen=True)
class TurnStateRequest:
    """这一轮是谁、在哪段对话、在哪执行（source 同 conversation.source：在用户设备上执行的是 device，
    业务服务上执行的是空串，IM 平台来的是平台标记）。"""

    workspace_id: str
    user_id: str
    conversation_id: str
    source: str
    answer_mode: str


TurnStateProvider = Callable[[TurnStateRequest], Awaitable[dict[str, Any] | None]]


class TurnStateHook:
    def __init__(self, *, timeout: float = TIMEOUT) -> None:
        self._providers: dict[str, TurnStateProvider] = {}
        self._timeout = timeout

    def add(self, name: str, provider: TurnStateProvider) -> None:
        if not EXTENSION_NAME_PATTERN.match(name) or name in self._providers:
            raise ValueError(f"扩展状态的名字不合规或重复：{name}")
        self._providers = {**self._providers, name: provider}

    def __bool__(self) -> bool:
        return bool(self._providers)

    async def collect(self, request: TurnStateRequest) -> dict[str, Any] | None:
        """这一轮的扩展状态；没登记时是 None（这一轮不带）。"""
        if not self._providers:
            return None
        names = tuple(self._providers)
        results = await asyncio.gather(*(asyncio.wait_for(self._providers[name](request), self._timeout)
                                         for name in names), return_exceptions=True)
        state: dict[str, Any] = {}
        for name, result in zip(names, results, strict=True):
            if isinstance(result, BaseException):
                log.warning("turn state %s for conversation %s unavailable", name, request.conversation_id,
                            exc_info=result)
            elif result is not None:
                state[name] = result
        return state
