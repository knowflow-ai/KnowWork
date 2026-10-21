"""运行调试视图的服务：读会话日志与用量，交给纯函数整理（events、digest、context）。只读，不新存任何东西。"""

from dataclasses import dataclass
from typing import Any, Protocol

from ..conversations.models import Conversation
from ..conversations.runlog import RawEvent, RunLog
from .context import StepInput, step_input
from .digest import TurnDetail, TurnSummary, detail, summarize
from .events import TurnSlice, main_events, slice_turns


class UsageSource(Protocol):
    """models.recorder.UsageRecorder 满足它。"""

    async def steps(self, workspace_id: str, conversation_id: str) -> tuple[Any, ...]: ...


@dataclass(frozen=True)
class TurnList:
    turns: tuple[TurnSummary, ...]
    truncated: bool  # 会话日志太长，只整理了前面一部分


class DebugService:
    def __init__(self, runlog: RunLog, usage: UsageSource) -> None:
        self._runlog = runlog
        self._usage = usage

    async def turns(self, conversation: Conversation) -> TurnList:
        page = await self._runlog.read(conversation.id)
        usage = await self._usage_map(conversation)
        return TurnList(turns=tuple(summarize(t, usage) for t in slice_turns(page.events)), truncated=page.truncated)

    async def turn(self, conversation: Conversation, turn: int) -> TurnDetail | None:
        found = await self._slice(conversation, turn)
        return None if found is None else detail(found, await self._usage_map(conversation))

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
