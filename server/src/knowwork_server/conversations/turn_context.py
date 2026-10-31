"""扩展给每一轮补的上下文（例如记忆）：扩展在业务服务启动时登记一个提供者，之后只读。

每轮发消息前问它一次，交回的两段文字（kernel/contract.TurnContext：常驻的、只这一轮的）随消息交给宿主，宿主照知识库目录
的办法放进模型上下文。没登记时这一轮什么都不带，和没有这个功能时一样；提供者出错或超时，这一轮也不带（宿主沿用上一轮的
常驻那段），不挡回答。
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ..kernel.contract import TurnContext

log = logging.getLogger(__name__)

TIMEOUT = 3.0  # 同知识库目录：这一轮最多等这么久，超了就不带


@dataclass(frozen=True)
class TurnContextRequest:
    """这一轮是谁、在哪、问了什么。user_id 是会话所属账号，渠道里的提问是渠道账号（扩展要分真人可以查 profiles 的 kind）。"""

    workspace_id: str
    user_id: str
    conversation_id: str
    text: str
    answer_mode: str


TurnContextProvider = Callable[[TurnContextRequest], Awaitable[TurnContext]]


class TurnContextHook:
    """只登记一个提供者：现在只有记忆要用，两个扩展抢同一段上下文时谁先谁后说不清，真有第二个再改成按顺序拼。"""

    def __init__(self, *, timeout: float = TIMEOUT) -> None:
        self._provider: TurnContextProvider | None = None
        self._timeout = timeout

    def set(self, provider: TurnContextProvider) -> None:
        if self._provider is not None:
            raise ValueError("turn context provider already set")
        self._provider = provider

    def __bool__(self) -> bool:
        return self._provider is not None

    async def collect(self, request: TurnContextRequest) -> TurnContext | None:
        """这一轮的上下文；没登记、出错或超时时返回 None（这一轮不带）。"""
        if self._provider is None:
            return None
        try:
            return await asyncio.wait_for(self._provider(request), self._timeout)
        except Exception:
            log.warning("turn context for conversation %s unavailable", request.conversation_id, exc_info=True)
            return None
