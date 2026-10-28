"""管理端的「运行记录」：全空间按时间列出每一轮，点开看与会话本人同样的运行详情（运行调试视图）。

- 列表读每轮汇总（conversations.summaries），可按提问人、状态、信号（检索了却没引用、被点踩）、工具、知识库筛；
  详情读原始帧；

- 要空间 owner 或 admin（workspace:edit，同审计页）；
- 运行详情里有用户的提问、文档内容与模型输出：管理员每次查看、导出别人的运行，都单独记一条审计
  （审计中间件只记写操作，这里是读，得自己记）。
"""

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

from ..audit.events import AuditEvent
from ..audit.sink import AuditSink
from ..conversations.models import Conversation
from ..conversations.summaries import SummaryQuery, SummaryRow
from .context import StepInput
from .digest import TurnDetail
from .service import DebugService

log = logging.getLogger(__name__)

MAX_RANGE = timedelta(days=92)
MAX_LIMIT = 100
WORKSPACE = "workspace"


class Forbidden(Exception):
    pass


class Invalid(Exception):
    pass


class AccessChecker(Protocol):
    def check(self, user_id: str, workspace_id: str, resource: str, action: str) -> bool: ...


class ConversationFinder(Protocol):
    """conversations.service.ConversationService 满足它；找不到抛 ConversationNotFound。"""

    async def find(self, *, conversation_id: str, workspace_id: str) -> Conversation: ...


class TurnTotals(Protocol):
    """models.recorder.UsageRecorder 满足它。"""

    async def turn_totals(self, workspace_id: str, conversation_ids: tuple[str, ...]) -> Mapping[tuple[str, int], Any]: ...


ProfileLookup = Callable[[Sequence[str]], Awaitable[Mapping[str, Any]]]


@dataclass(frozen=True)
class RunRow:
    conversation_id: str
    title: str
    user_id: str
    user_name: str | None
    turn: int
    started_at: str
    status: str  # running | completed | cancelled | failed
    duration_ms: int | None
    model_calls: int
    input_tokens: int
    output_tokens: int
    cost: str | None
    question: str = ""
    tools: tuple[str, ...] = ()
    searches: int | None = None  # 这一轮检索知识库几次；没检索过为空
    cited: int | None = None     # 回答引用了几条
    rating: str | None = None    # like | dislike


@dataclass(frozen=True)
class RunPage:
    items: tuple[RunRow, ...]
    next: str | None  # 下一页的 before


class RunRecords:
    def __init__(self, summaries: SummaryQuery, usage: TurnTotals, debug: DebugService,
                 conversations: ConversationFinder, access: AccessChecker, audit: AuditSink,
                 profiles: ProfileLookup | None = None) -> None:
        self._summaries = summaries
        self._usage = usage
        self._debug = debug
        self._conversations = conversations
        self._access = access
        self._audit = audit
        self._profiles = profiles

    async def runs(self, actor: str, workspace_id: str, *, since: datetime, until: datetime,
                   before: datetime | None = None, user_id: str | None = None, status: str | None = None,
                   signal: str | None = None, tool: str | None = None, base: str | None = None,
                   limit: int = 50) -> RunPage:
        self._require(actor, workspace_id)
        if until <= since or until - since > MAX_RANGE:
            raise Invalid(f"时间范围须在 {MAX_RANGE.days} 天以内")
        limit = max(1, min(limit, MAX_LIMIT))
        found = await self._summaries.page(workspace_id, since=since, until=until, before=before, user_id=user_id,
                                           status=status, signal=signal, tool=tool, base=base, limit=limit)
        rows = await self._rows(workspace_id, found)
        return RunPage(items=rows, next=rows[-1].started_at if rows and len(found) == limit else None)

    async def turn(self, actor: str, workspace_id: str, conversation_id: str, turn: int) -> TurnDetail | None:
        conversation = await self._viewed(actor, workspace_id, conversation_id, "run.view", turn=turn)
        return await self._debug.turn(conversation, turn)

    async def step_input(self, actor: str, workspace_id: str, conversation_id: str, turn: int,
                         step: int) -> StepInput | None:
        conversation = await self._viewed(actor, workspace_id, conversation_id, "run.view_input", turn=turn, step=step)
        return await self._debug.step_input(conversation, turn, step)

    async def export(self, actor: str, workspace_id: str, conversation_id: str, turn: int) -> list[dict] | None:
        conversation = await self._viewed(actor, workspace_id, conversation_id, "run.export", turn=turn)
        return await self._debug.export(conversation, turn)

    def _require(self, actor: str, workspace_id: str) -> None:
        if not self._access.check(actor, workspace_id, WORKSPACE, "edit"):
            raise Forbidden()

    async def _viewed(self, actor: str, workspace_id: str, conversation_id: str, action: str,
                      **detail: int) -> Conversation:
        self._require(actor, workspace_id)
        conversation = await self._conversations.find(conversation_id=conversation_id, workspace_id=workspace_id)
        if conversation.user_id != actor:
            await self._audit.record(AuditEvent(
                workspace_id=workspace_id, actor_type="human", actor_id=actor, action=action, result="success",
                resource_type="conversation", resource_id=conversation_id, session_id=conversation_id,
                detail={**detail, "owner": conversation.user_id}))
        return conversation

    async def _rows(self, workspace_id: str, found: tuple[SummaryRow, ...]) -> tuple[RunRow, ...]:
        ids = tuple(dict.fromkeys(row.summary.conversation_id for row in found))
        totals = await self._usage.turn_totals(workspace_id, ids)
        names = await self._names(tuple(dict.fromkeys(row.summary.user_id for row in found)))
        return tuple(_row(row, totals.get((row.summary.conversation_id, row.summary.turn)),
                          names.get(row.summary.user_id)) for row in found)

    async def _names(self, user_ids: tuple[str, ...]) -> Mapping[str, str]:
        if self._profiles is None or not user_ids:
            return {}
        try:
            found = await self._profiles(user_ids)
        except Exception:
            log.warning("member profile lookup failed; listing runs without names", exc_info=True)
            return {}
        return {uid: str(getattr(profile, "name", "") or getattr(profile, "email", "")) for uid, profile in found.items()}


def _row(found: SummaryRow, totals: Any, name: str | None) -> RunRow:
    summary = found.summary
    ended = summary.ended_at
    duration = int((ended - summary.started_at).total_seconds() * 1000) if ended is not None else None
    cost = getattr(totals, "cost", None)
    knowledge = (summary.facts or {}).get("knowledge") or {}
    return RunRow(conversation_id=summary.conversation_id, title=found.title, user_id=summary.user_id,
                  user_name=name, turn=summary.turn, started_at=summary.started_at.isoformat(), status=summary.status,
                  duration_ms=duration, model_calls=getattr(totals, "calls", 0),
                  input_tokens=getattr(totals, "input_tokens", 0), output_tokens=getattr(totals, "output_tokens", 0),
                  cost=None if cost is None else format(Decimal(cost).normalize(), "f"), question=summary.question,
                  tools=tuple(summary.tools or ()), searches=knowledge.get("searches"), cited=knowledge.get("cited"),
                  rating=found.rating)
