"""回合结束时收产物：记下事件流里官方 present 发出的 deliverables/presented，turn/end 时在后台调 collector.collect。

只在回合正常完成（completed）时收，收的是这一回合交付过的路径、回合结束时的内容（present 交付的是当前文件，
不是调用那一刻的快照）；被取消或失败的回合交付记录直接丢掉，模型下一轮要交付得再调一次 present。
收取的提示（超限、不可读等）当前只写日志，推给用户的那一步等事件流支持系统消息后接上。

收取在后台跑，而前端收到 run.completed 就会来列产物：settle(conversation_id) 等这个会话在收的产物
落库后再返回（有上限，超时就按已有的列），列表接口在查询前调用它，免得刚生成的文件这一轮看不到。
事件泵在推送 run.completed 与调用观察者之间没有 await，所以前端拿到事件时收取任务一定已经登记。
"""

import asyncio
import logging
from typing import Any

from .collector import ArtifactCollector

log = logging.getLogger(__name__)
SETTLE_TIMEOUT_SECONDS = 15.0


def turn_run_id(turn: int) -> str:
    """产物记在哪一轮（artifact.run_id）。技能运行事实按它数本轮的产物。"""
    return f"run-{turn}"


class ArtifactObserver:
    def __init__(self, collector: ArtifactCollector) -> None:
        self._collector = collector
        self._tasks: set[asyncio.Task] = set()
        self._by_conversation: dict[str, set[asyncio.Task]] = {}
        # 每个会话本回合交付过的路径 → 说明（没写是 None）；同一路径交付多次用最近一次的说明（同官方交付卡片）
        self._presented: dict[str, dict[str, str | None]] = {}

    def __call__(self, conversation_id: str, workspace_id: str, kind: str, data: Any) -> None:
        if kind != "session/event" or not isinstance(data, dict):
            return
        payload = data.get("data") if isinstance(data.get("data"), dict) else {}
        if data.get("type") == "deliverables/presented":
            files = payload.get("files") if isinstance(payload.get("files"), list) else []
            presented = self._presented.setdefault(conversation_id, {})
            for file in files:
                if isinstance(file, dict) and isinstance(file.get("path"), str):
                    description = file.get("description")
                    presented[file["path"]] = description if isinstance(description, str) else None
            return
        if data.get("type") != "turn/end":
            return
        presented = self._presented.pop(conversation_id, {})
        reason = payload.get("reason") if isinstance(payload.get("reason"), dict) else {}
        if reason.get("kind") != "completed" or not presented:
            return
        turn = payload.get("turn")
        run_id = turn_run_id(turn) if isinstance(turn, int) else None
        task = asyncio.create_task(self._collect(workspace_id, conversation_id, run_id, presented))
        self._tasks.add(task)
        self._by_conversation.setdefault(conversation_id, set()).add(task)
        task.add_done_callback(lambda done: self._forget(conversation_id, done))

    def _forget(self, conversation_id: str, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        pending = self._by_conversation.get(conversation_id)
        if pending is None:
            return
        pending.discard(task)
        if not pending:
            del self._by_conversation[conversation_id]

    async def settle(self, conversation_id: str, timeout: float = SETTLE_TIMEOUT_SECONDS) -> None:
        """等这个会话在收的产物落库；超时不报错，调用方按已落库的继续。"""
        pending = list(self._by_conversation.get(conversation_id, ()))
        if not pending:
            return
        _done, late = await asyncio.wait(pending, timeout=timeout)
        if late:
            log.warning("artifacts for %s still collecting after %.0fs", conversation_id, timeout)

    async def _collect(self, workspace_id: str, conversation_id: str, run_id: str | None,
                       presented: dict[str, str | None]) -> None:
        descriptions = {path: text for path, text in presented.items() if text is not None}
        try:
            result = await self._collector.collect(workspace_id, conversation_id, run_id=run_id,
                                                   paths=list(presented), descriptions=descriptions)
        except Exception:
            log.exception("collecting artifacts for %s failed", conversation_id)
            return
        if result.message:
            log.info("artifacts for %s: %s", conversation_id, result.message)

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
