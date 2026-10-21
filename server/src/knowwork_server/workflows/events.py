"""运行与步骤状态的事件流（单独一个 SSE，不复用会话的事件流）。

为什么不复用 conversations 的事件流：那条流的序号由会话事件泵按宿主帧分配（run_event 的主键是
(conversation_id, seq)，并且与宿主 epoch/cursor 一一对应），工作流运行既不一定属于某个会话，状态变化
也不是宿主帧；硬塞进去要么另起一套序号与泵抢，要么伪造宿主帧。单独一条流改动最小。

推送的是「状态快照」而不是增量：workflow_run.version 在运行或任一步骤变化时加一，客户端带上最后收到
的 version（Last-Event-ID 或 ?after=）重连，服务端只要库里的 version 更大就推一份完整快照。
本进程内的变化立即唤醒订阅者；别的实例推进的运行靠定时轮询兜底（默认 2 秒）。
"""

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Iterator

from .errors import RunNotFound
from .store import WorkflowStore

EVENT = "workflow.run"


class RunNotifier:
    def __init__(self) -> None:
        self._waiters: dict[str, set[asyncio.Event]] = {}

    def notify(self, run_id: str) -> None:
        for event in tuple(self._waiters.get(run_id, ())):
            event.set()

    @contextlib.contextmanager
    def listen(self, run_id: str) -> Iterator[asyncio.Event]:
        event = asyncio.Event()
        self._waiters.setdefault(run_id, set()).add(event)
        try:
            yield event
        finally:
            waiters = self._waiters.get(run_id)
            if waiters is not None:
                waiters.discard(event)
                if not waiters:
                    self._waiters.pop(run_id, None)


def parse_after(last_event_id: str | None, after: int) -> int:
    if last_event_id and last_event_id.strip().isdigit():
        return int(last_event_id.strip())
    return max(after, 0)


async def run_snapshots(store: WorkflowStore, notifier: RunNotifier, workspace_id: str, run_id: str, after: int, *,
                        poll_seconds: float = 2.0) -> AsyncIterator[dict]:
    """version 比 after 大就推一份快照；运行到终态推完最后一份即结束。"""
    with notifier.listen(run_id) as changed:
        while True:
            changed.clear()
            try:
                view = await store.load(workspace_id, run_id)
            except RunNotFound:
                return
            if view.version > after:
                after = view.version
                yield {"id": str(view.version), "event": EVENT, "data": json.dumps(view.to_json(), ensure_ascii=False)}
            if view.finished:
                return
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(changed.wait(), timeout=poll_seconds)
