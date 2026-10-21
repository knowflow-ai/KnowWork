"""实时推送：每个正在看会话的浏览器连接一个监听者。

监听者跟不上（积压超过上限）就被踢出并标记关闭；读的一侧看到关闭就结束 SSE，
浏览器带 Last-Event-ID 重连，从库里按序号补齐。踢出不需要往已满的队列里再塞东西。
"""

import asyncio
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

DEFAULT_LISTENER_BACKLOG = 1000


@dataclass(frozen=True)
class StoredEvent:
    """一帧落库（或待落库）后的形态：会话内序号、原始帧、以及它投影出的 events.v1 事件。"""

    conversation_id: str
    seq: int
    kind: str
    data: object
    ui: tuple[dict, ...]


class Listener:
    def __init__(self, backlog: int) -> None:
        self._backlog = backlog
        self._items: deque[StoredEvent] = deque()
        self._signal = asyncio.Event()
        self.closed = False

    def offer(self, event: StoredEvent) -> bool:
        """放入一条；积压超限时关闭自己并返回 False。"""
        if self.closed:
            return False
        if len(self._items) >= self._backlog:
            self.close()
            return False
        self._items.append(event)
        self._signal.set()
        return True

    def close(self) -> None:
        self.closed = True
        self._items.clear()
        self._signal.set()

    async def next_batch(self) -> list[StoredEvent] | None:
        """等到有新事件；返回 None 表示已被关闭，调用方应结束推送。"""
        while not self._items and not self.closed:
            self._signal.clear()
            await self._signal.wait()
        if self.closed:
            return None
        batch = list(self._items)
        self._items.clear()
        return batch


class Broadcaster:
    def __init__(self, backlog: int = DEFAULT_LISTENER_BACKLOG) -> None:
        self._backlog = backlog
        self._listeners: dict[str, set[Listener]] = {}

    def count(self, conversation_id: str) -> int:
        return len(self._listeners.get(conversation_id, ()))

    @asynccontextmanager
    async def listen(self, conversation_id: str) -> AsyncIterator[Listener]:
        listener = Listener(self._backlog)
        self._listeners.setdefault(conversation_id, set()).add(listener)
        try:
            yield listener
        finally:
            self._discard(conversation_id, listener)

    def publish(self, event: StoredEvent) -> None:
        for listener in list(self._listeners.get(event.conversation_id, ())):
            if not listener.offer(event):
                self._discard(event.conversation_id, listener)

    def close_all(self) -> None:
        for listeners in self._listeners.values():
            for listener in list(listeners):
                listener.close()
        self._listeners.clear()

    def _discard(self, conversation_id: str, listener: Listener) -> None:
        listeners = self._listeners.get(conversation_id)
        if listeners is None:
            return
        listeners.discard(listener)
        if not listeners:
            del self._listeners[conversation_id]
