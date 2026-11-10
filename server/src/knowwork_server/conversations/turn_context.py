"""扩展给每一轮补的上下文（例如记忆、问数的示例）：扩展在业务服务启动时登记提供者，之后只读。
每轮发消息前并行问一遍，交回的两段文字（kernel/contract.TurnContext：常驻的、只这一轮的）按登记顺序拼起来，随消息交给
宿主，宿主照知识库目录的办法放进模型上下文。没登记时这一轮什么都不带，和没有这个功能时一样。

有一个提供者出错或超时，这一轮整段都不带（宿主沿用上一轮的常驻那段）：只拼上算出来的那几段，常驻那段就少了一块，
宿主会当成它不再适用。所以提供者自己能降级的（例如召回服务挂了就不给示例）要自己接住、交回空的，别连累别的提供者。
"""
import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ..kernel.contract import TurnContext

log = logging.getLogger(__name__)

TIMEOUT = 3.0  # 同知识库目录：这一轮最多等这么久，超了就不带
STANDING_MAX = TurnContext.model_fields["standing"].metadata[0].max_length
TURN_MAX = TurnContext.model_fields["turn"].metadata[0].max_length


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
    def __init__(self, *, timeout: float = TIMEOUT) -> None:
        self._providers: tuple[TurnContextProvider, ...] = ()
        self._timeout = timeout

    def add(self, provider: TurnContextProvider) -> None:
        self._providers = (*self._providers, provider)

    def __bool__(self) -> bool:
        return bool(self._providers)

    async def collect(self, request: TurnContextRequest) -> TurnContext | None:
        """这一轮的上下文；没登记、有提供者出错或超时时返回 None（这一轮不带）。"""
        if not self._providers:
            return None
        try:
            parts = await asyncio.gather(*(asyncio.wait_for(provider(request), self._timeout)
                                           for provider in self._providers))
        except Exception:
            log.warning("turn context for conversation %s unavailable", request.conversation_id, exc_info=True)
            return None
        # 身份只能有一个：先登记的扩展说了算
        return TurnContext(standing=_joined([p.standing for p in parts], STANDING_MAX),
                           turn=_joined([p.turn for p in parts], TURN_MAX),
                           persona=next((p.persona for p in parts if p.persona), ""))


def _joined(texts: list[str], limit: int) -> str:
    """按登记顺序拼，空的跳过；超了合同上限从后面截（先登记的在前、保得住）。"""
    return "\n\n".join(text for text in texts if text)[:limit]
