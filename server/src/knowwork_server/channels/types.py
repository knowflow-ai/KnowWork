"""渠道与平台无关的部分：入站消息、各平台要实现的回发能力。"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

ChatType = Literal["direct", "group"]


@dataclass(frozen=True)
class IncomingMessage:
    """一条进来的消息，已经去掉群里开头的「@机器人」。raw 是平台原始帧，回复时平台适配器要用。"""

    channel_id: str
    workspace_id: str
    platform: str
    external_id: str  # 平台用户 id（企业微信 userid）
    chat_type: ChatType
    chat_id: str  # 群 id；私聊为空
    message_id: str
    text: str
    display_name: str = ""
    raw: Any = field(default=None, compare=False, repr=False)

    @property
    def session_key(self) -> str:
        """对话位置：私聊按人，群里按（群, 人），同一群里每人一个会话。"""
        if self.chat_type == "group":
            return f"group:{self.chat_id}:{self.external_id}"
        return f"dm:{self.external_id}"


@dataclass(frozen=True)
class Picture:
    """回答里的一张图（知识库里的图，已按渠道账号的权限取回）。答完时随最后一帧发出，排在回答后面。"""

    data: bytes = field(repr=False)
    mime: str  # image/png、image/jpeg 等
    caption: str = ""


class ReplyStream(Protocol):
    """一条可以反复替换内容的回复（企业微信的流式消息、飞书卡片）。update / finish 给的是到目前为止的全文。

    finish 带上回答里的图：各平台尽力发，发不了的（格式、大小不合平台要求）跳过，不影响文字。"""

    async def update(self, text: str) -> None: ...

    async def finish(self, text: str, pictures: Sequence[Picture] = ()) -> None: ...


class Connection(Protocol):
    """一个机器人连接（一个渠道）。"""

    async def reply(self, message: IncomingMessage, text: str) -> None: ...

    async def stream(self, message: IncomingMessage, placeholder: str) -> ReplyStream: ...


# 收到一条消息时调用；平台适配器只管把平台帧转成 IncomingMessage 交上来
MessageHandler = Callable[[IncomingMessage, Connection], Awaitable[None]]
# 连接状态变化时调用：connecting / connected / error（带原因）
StatusReporter = Callable[[str, str], Awaitable[None]]
