"""运行调试视图的服务：读会话日志与用量，交给纯函数整理（events、digest、context）。只读，不新存任何东西。

工具调用的过程（例如知识检索是怎么查的）不在会话日志里，由业务按 callId 另记；给了 traces 就按这一轮的 callId 取来
挂到对应的调用上（knowledge.search_log 满足它）。
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol

from ..conversations.models import Conversation
from ..conversations.runlog import RawEvent, RunLog
from .background import background
from .context import StepInput, step_input
from .digest import StepView, TurnDetail, TurnSummary, detail, summarize
from .events import TurnSlice, main_events, slice_turns


class UsageSource(Protocol):
    """models.recorder.UsageRecorder 满足它。"""

    async def steps(self, workspace_id: str, conversation_id: str) -> tuple[Any, ...]: ...


class CallTraces(Protocol):
    """knowledge.search_log.SearchLog 满足它。"""

    async def traces(self, workspace_id: str, conversation_id: str,
                     call_ids: Sequence[str]) -> dict[str, list[dict]]: ...


@dataclass(frozen=True)
class TurnList:
    turns: tuple[TurnSummary, ...]
    truncated: bool  # 会话日志太长，只整理了前面一部分


class DebugService:
    def __init__(self, runlog: RunLog, usage: UsageSource, traces: CallTraces | None = None) -> None:
        self._runlog = runlog
        self._usage = usage
        self._traces = traces

    async def turns(self, conversation: Conversation) -> TurnList:
        page = await self._runlog.read(conversation.id)
        usage = await self._usage_map(conversation)
        return TurnList(turns=tuple(summarize(t, usage) for t in slice_turns(page.events)), truncated=page.truncated)

    async def turn(self, conversation: Conversation, turn: int) -> TurnDetail | None:
        turns = slice_turns((await self._runlog.read(conversation.id)).events)
        found = next((t for t in turns if t.turn == turn), None)
        if found is None:
            return None
        built = replace(detail(found, await self._usage_map(conversation)), background=background(turns, turn))
        return await self._with_traces(conversation, built)

    async def _with_traces(self, conversation: Conversation, turn: TurnDetail) -> TurnDetail:
        if self._traces is None:
            return turn
        call_ids = [c.call_id for s in (*turn.steps, *(s for a in turn.subagents for s in a.steps)) for c in s.tool_calls]
        found = await self._traces.traces(conversation.workspace_id, conversation.id, call_ids)
        if not found:
            return turn

        def steps(items: tuple[StepView, ...]) -> tuple[StepView, ...]:
            return tuple(replace(s, tool_calls=tuple(replace(c, trace=tuple(found.get(c.call_id, ())))
                                                     for c in s.tool_calls)) for s in items)

        return replace(turn, steps=steps(turn.steps),
                       subagents=tuple(replace(a, steps=steps(a.steps)) for a in turn.subagents))

    async def step_input(self, conversation: Conversation, turn: int, step: int) -> StepInput | None:
        page = await self._runlog.read(conversation.id)
        result = step_input(main_events(page.events), turn, step)
        return result if result.found else None

    async def export(self, conversation: Conversation, turn: int) -> list[dict[str, Any]] | None:
        """这一轮的原始帧（含子 Agent 的），原样给下载，方便离线分析或附在问题报告里。"""
        found = await self._slice(conversation, turn)
        return None if found is None else [_raw(event) for event in found.raw]

    async def _slice(self, conversation: Conversation, turn: int) -> TurnSlice | None:
        page = await self._runlog.read(conversation.id)
        return next((t for t in slice_turns(page.events) if t.turn == turn), None)

    async def _usage_map(self, conversation: Conversation) -> dict[tuple[int, int], Any]:
        records = await self._usage.steps(conversation.workspace_id, conversation.id)
        return {(record.turn, record.step): record for record in records}


def _raw(event: RawEvent) -> dict[str, Any]:
    return {"seq": event.seq, "kind": event.kind, "data": event.data,
            "receivedAt": event.at.isoformat() if event.at else None}
