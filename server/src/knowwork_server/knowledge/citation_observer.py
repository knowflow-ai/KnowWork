"""回合结束时校验回答里的引用编号。

观察事件流：记下每个会话本轮最后一条助手回答的文本，回合正常完成时用引用目录核对 [n]。
不存在的编号写一条警告日志，最近一次的核对结果按会话留着供诊断；回答原文由宿主事件流决定，
这里改不了它，界面上去掉无效编号由前端按出处接口（citation_api）完成，两边用同一份引用目录。

同产物观察者一样：在泵的帧处理里同步调用，核对放到后台任务，失败只记日志。
"""

import asyncio
import logging
from typing import Any

from ..conversations.events_v1 import assistant_text
from .citation_store import CitationStore
from .citations import CitationCheck

log = logging.getLogger(__name__)
_MAX_CHECKS = 1000


class CitationObserver:
    def __init__(self, citations: CitationStore) -> None:
        self._citations = citations
        self._answers: dict[str, str] = {}
        self._checks: dict[str, CitationCheck] = {}
        self._tasks: set[asyncio.Task] = set()

    def __call__(self, conversation_id: str, workspace_id: str, kind: str, data: Any) -> None:
        if kind != "session/event" or not isinstance(data, dict):
            return
        payload = data.get("data")
        if data.get("type") == "assistant/message":
            text = assistant_text(payload)
            if text:
                self._answers = {**self._answers, conversation_id: text}
            return
        if data.get("type") != "turn/end":
            return
        answer = self._answers.get(conversation_id, "")
        self._answers = {k: v for k, v in self._answers.items() if k != conversation_id}
        reason = payload.get("reason") if isinstance(payload, dict) else None
        if not answer or not isinstance(reason, dict) or reason.get("kind") != "completed":
            return
        task = asyncio.create_task(self._check(workspace_id, conversation_id, answer))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _check(self, workspace_id: str, conversation_id: str, answer: str) -> None:
        try:
            check = await self._citations.check_answer(workspace_id, conversation_id, answer)
        except Exception:
            log.exception("checking citations for %s failed", conversation_id)
            return
        if not check.cited and not check.removed:
            return
        kept = dict(list(self._checks.items())[-(_MAX_CHECKS - 1):])
        self._checks = {**kept, conversation_id: check}
        for warning in check.warnings:
            log.warning("citations in %s: %s", conversation_id, warning)

    def last_check(self, conversation_id: str) -> CitationCheck | None:
        return self._checks.get(conversation_id)

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
