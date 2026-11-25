"""官方会话标题同步进对话：事件泵看到 session/title（官方 dsh-session-title 写进会话日志的标题）就写对话标题。

对话标题以官方为准：官方按第一句话给个兜底标题，再用模型起一个短标题（dsh-session-title、
dsh-session-title-first-prompt-llm，两种宿主都挂）；用户在页面上改名时业务服务经内核合同写进官方标题（ConversationService.rename），
回来的同一个标题这里写一遍是空操作，不回写宿主，不会来回打转。在设备上执行的对话，事件经上传进同一个事件泵，一样同步。

同一段对话先后来几个标题时只写最后一个，一次只写一个（不让后来的先落库、再被先来的盖掉）。
"""

import asyncio
import logging
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..access.rls import set_workspace
from .models import Conversation

log = logging.getLogger(__name__)
TITLE_EVENT = "session/title"
MAX_TITLE = 200


def title_of(kind: str, data: Any) -> str | None:
    """这一帧带来的官方标题；不是标题事件、标题为空返回 None。"""
    if kind != "session/event" or not isinstance(data, dict) or data.get("type") != TITLE_EVENT:
        return None
    payload = data.get("data")
    title = payload.get("title") if isinstance(payload, dict) else None
    if not isinstance(title, str):
        return None
    single = " ".join(title.split())
    return single[:MAX_TITLE] or None


class TitleObserver:
    def __init__(self, sessionmaker: async_sessionmaker) -> None:
        self._sessionmaker = sessionmaker
        # 对话 → (空间, 还没写的最新标题)；对话 → 正在写它的任务
        self._latest: dict[str, tuple[str, str]] = {}
        self._writers: dict[str, asyncio.Task] = {}

    def __call__(self, conversation_id: str, workspace_id: str, kind: str, data: Any) -> None:
        title = title_of(kind, data)
        if title is None:
            return
        self._latest[conversation_id] = (workspace_id, title)
        if conversation_id not in self._writers:
            task = asyncio.create_task(self._drain(conversation_id))
            self._writers[conversation_id] = task
            task.add_done_callback(lambda _done: self._writers.pop(conversation_id, None))

    async def _drain(self, conversation_id: str) -> None:
        while (pending := self._latest.pop(conversation_id, None)) is not None:
            workspace_id, title = pending
            try:
                async with self._sessionmaker() as session, session.begin():
                    await set_workspace(session, workspace_id)
                    await session.execute(update(Conversation).where(
                        Conversation.id == conversation_id, Conversation.title != title).values(title=title))
            except Exception:
                log.exception("syncing the official title of %s failed", conversation_id)

    async def drain(self) -> None:
        """等还没写完的标题写完（关闭时调）。"""
        while self._writers:
            await asyncio.gather(*list(self._writers.values()), return_exceptions=True)
