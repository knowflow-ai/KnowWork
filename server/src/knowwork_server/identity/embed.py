"""网页嵌入的会话令牌怎么认（channels/web_tokens.py 签发与核对）。

令牌由渠道模块签发与核对（channels/web.py：签名、过期、渠道还在还开着），这里只登记它的解析函数，认人的规则在
container.py：嵌入令牌只给声明了范围、而且嵌入身份带齐这些范围的接口用（EMBED_SCOPES，目前只有对话），
不查范围的接口一律不认。没登记解析函数（渠道模块没装配）时，嵌入令牌和伪造的一样处理。
"""

from collections.abc import Awaitable, Callable

from ..auth.principal import Principal

EMBED_PREFIX = "emb_"
# 嵌入身份能用的接口范围：对话（新建、发消息、事件流、停止、回答提问……），别的都不给
EMBED_SCOPES = frozenset({"conversations:use"})

EmbedResolver = Callable[[str], Awaitable[Principal | None]]


class EmbedCredentials:
    def __init__(self) -> None:
        self._resolve: EmbedResolver | None = None

    def set(self, resolve: EmbedResolver) -> None:
        self._resolve = resolve

    async def resolve(self, token: str) -> Principal | None:
        if self._resolve is None:
            return None
        principal = await self._resolve(token)
        if principal is None or not principal.is_embedded:
            return None
        return principal
