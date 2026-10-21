"""事件泵的用量观察者：把宿主事件流转成用量记录，并在单轮模型调用超限时取消本轮。

观察者在泵的帧处理里被同步调用，必须立即返回且不抛异常；写库与取消放到后台任务里做，
失败只记日志。每个会话记住当前模型别名（来自 request/header）与本轮调用计数。
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from .usage import TurnCallBudget, UsageContext, UsageEntry, model_from_header, usage_from_event

log = logging.getLogger(__name__)

OwnerLookup = Callable[[str], Awaitable[str | None]]
CancelTurn = Callable[[str], Awaitable[None]]


class UsageObserver:
    def __init__(self, recorder, owner_of: OwnerLookup, cancel_turn: CancelTurn, *, default_model: str,
                 max_calls_per_turn: int) -> None:
        self._recorder = recorder
        self._owner_of = owner_of
        self._cancel_turn = cancel_turn
        self._default_model = default_model
        self._limit = max_calls_per_turn
        self._models: dict[str, str] = {}
        self._budgets: dict[str, TurnCallBudget] = {}
        self._cancelled_turns: dict[str, int | None] = {}
        self._tasks: set[asyncio.Task] = set()

    def __call__(self, conversation_id: str, workspace_id: str, kind: str, data: Any) -> None:
        if kind != "session/event" or not isinstance(data, dict):
            return
        event_type, payload = data.get("type"), data.get("data")
        if not isinstance(event_type, str):
            return
        model = model_from_header(event_type, payload)
        if model is not None:
            self._models = {**self._models, conversation_id: model}
        self._count_call(conversation_id, event_type, payload)
        context = UsageContext(workspace_id=workspace_id, user_id="", conversation_id=conversation_id,
                               model_alias=self._models.get(conversation_id, self._default_model))
        entry = usage_from_event(event_type, payload, context, datetime.now(UTC))
        if entry is not None:
            self._spawn(self._record(entry))

    def _count_call(self, conversation_id: str, event_type: str, payload: Any) -> None:
        budget = self._budgets.get(conversation_id, TurnCallBudget(self._limit)).after(event_type, payload)
        self._budgets = {**self._budgets, conversation_id: budget}
        if budget.exceeded and self._cancelled_turns.get(conversation_id) != budget.turn:
            self._cancelled_turns = {**self._cancelled_turns, conversation_id: budget.turn}
            log.warning("conversation %s exceeded %d model calls in turn %s; cancelling",
                        conversation_id, self._limit, budget.turn)
            self._spawn(self._cancel(conversation_id))

    async def _record(self, entry: UsageEntry) -> None:
        try:
            user_id = await self._owner_of(entry.conversation_id)
            if user_id is None:
                log.warning("usage for unknown conversation %s dropped", entry.conversation_id)
                return
            await self._recorder.record(replace(entry, user_id=user_id))
        except Exception:
            log.exception("recording usage for %s failed", entry.conversation_id)

    async def _cancel(self, conversation_id: str) -> None:
        try:
            await self._cancel_turn(conversation_id)
        except Exception:
            log.exception("cancelling over-budget turn for %s failed", conversation_id)

    def _spawn(self, coroutine) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        """等后台写入与取消完成（停机与测试用）。"""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
