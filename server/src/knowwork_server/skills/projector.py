"""对话里技能运行事实的投影器（技术方案 6.6 节）：事件泵的观察者，回合结束后在后台把这一轮写成
skill_run / skill_step_run。

不拖慢对话：泵的帧处理里只记一下「这个会话本轮加载过技能」，不做任何 IO；回合结束才起后台任务，
没加载技能的回合什么都不做。后台任务先等这一轮的帧落库（落库是批量异步的，见 conversations/writer.py）、
本轮产物收完，再从库里读这一轮的日志与用量投影（facts.py）。失败只记日志；投影是幂等的，可以按轮重算（project）。

不从 skills/__init__ 导出：它依赖会话与产物，而产物模块又引用 skills.errors，放进包入口会成环。
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import async_sessionmaker

from ..artifacts.observer import turn_run_id
from ..conversations.listeners import StoredEvent
from ..conversations.models import Conversation
from ..conversations.runlog import RawEvent, RunLog
from ..debugview.events import slice_turns
from .facts import RunFact, StepUsageLike, project_turn
from .runs import RunFinish, RunStart, SkillRunStore, StepRecord, TurnRun

log = logging.getLogger(__name__)

WRITE_TIMEOUT_SECONDS = 30.0
POLL_SECONDS = 0.05

PendingFrames = Callable[[str], Sequence[StoredEvent]]  # BatchWriter.pending：还没提交的帧
SettleArtifacts = Callable[[str], Awaitable[None]]  # ArtifactObserver.settle：等本会话在收的产物落库


class UsageSteps(Protocol):
    async def steps(self, workspace_id: str, conversation_id: str) -> Sequence[StepUsageLike]: ...


class HasRunId(Protocol):
    run_id: str | None


class ArtifactLister(Protocol):
    async def list(self, conversation: Conversation) -> Sequence[HasRunId]: ...


@dataclass(frozen=True)
class TurnInputs:
    user_id: str
    events: tuple[RawEvent, ...]
    truncated: bool
    usage: tuple[StepUsageLike, ...]
    artifact_count: int


class LoggedTurns:
    """从库里取投影一轮要的东西。sessionmaker 要声明好工作空间（与会话回放同一个，run_event 开了行级安全）。"""

    def __init__(self, sessionmaker: async_sessionmaker, usage: UsageSteps, artifacts: ArtifactLister) -> None:
        self._sessionmaker = sessionmaker
        self._runlog = RunLog(sessionmaker)
        self._usage = usage
        self._artifacts = artifacts

    async def read(self, workspace_id: str, conversation_id: str, turn: int) -> TurnInputs | None:
        async with self._sessionmaker() as session:
            conversation = await session.get(Conversation, conversation_id)
        if conversation is None:  # 回合结束后会话马上被删了
            return None
        page = await self._runlog.turn_events(conversation_id, turn)
        usage = await self._usage.steps(workspace_id, conversation_id)
        run_id = turn_run_id(turn)
        artifacts = sum(1 for item in await self._artifacts.list(conversation) if item.run_id == run_id)
        return TurnInputs(user_id=conversation.user_id, events=page.events, truncated=page.truncated,
                          usage=tuple(usage), artifact_count=artifacts)


class TurnReader(Protocol):
    async def read(self, workspace_id: str, conversation_id: str, turn: int) -> TurnInputs | None: ...


def run_id_of(conversation_id: str, turn: int) -> str:
    return f"{conversation_id}:{turn}"


class SkillRunProjector:
    def __init__(self, turns: TurnReader, store: SkillRunStore, pending: PendingFrames,
                 settle: SettleArtifacts) -> None:
        self._turns = turns
        self._store = store
        self._pending = pending
        self._settle = settle
        self._loaded: frozenset[str] = frozenset()  # 本轮加载过技能的会话
        self._tasks: set[asyncio.Task] = set()

    def __call__(self, conversation_id: str, workspace_id: str, kind: str, data: Any) -> None:
        if kind == "skill/loaded":
            self._loaded = self._loaded | {conversation_id}
            return
        if kind != "session/event" or not isinstance(data, dict) or data.get("type") != "turn/end":
            return
        if conversation_id not in self._loaded:
            return
        self._loaded = self._loaded - {conversation_id}
        payload = data.get("data") if isinstance(data.get("data"), dict) else {}
        turn = payload.get("turn")
        if type(turn) is not int:
            return
        # 泵先把帧交给落库器再调观察者，所以此刻待写帧里一定有这一轮的 turn/end
        upto = max((event.seq for event in self._pending(conversation_id)), default=None)
        task = asyncio.create_task(self._after_turn(workspace_id, conversation_id, turn, upto))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _after_turn(self, workspace_id: str, conversation_id: str, turn: int, upto: int | None) -> None:
        try:
            await self._written(conversation_id, upto)
            await self._settle(conversation_id)
            await self.project(workspace_id, conversation_id, turn)
        except Exception:
            log.exception("projecting skill runs for %s turn %s failed", conversation_id, turn)

    async def _written(self, conversation_id: str, upto: int | None) -> None:
        """等这个会话序号不超过 upto 的帧都提交。超时就按库里已有的投影，之后可以重算。"""
        if upto is None:
            return
        try:
            async with asyncio.timeout(WRITE_TIMEOUT_SECONDS):
                while any(event.seq <= upto for event in self._pending(conversation_id)):
                    await asyncio.sleep(POLL_SECONDS)
        except TimeoutError:
            log.warning("run events of %s up to %d still unwritten after %.0fs; projecting what is stored",
                        conversation_id, upto, WRITE_TIMEOUT_SECONDS)

    async def project(self, workspace_id: str, conversation_id: str, turn: int) -> int:
        """（重新）投影某一轮，幂等：整轮替换。返回写入的运行数。"""
        inputs = await self._turns.read(workspace_id, conversation_id, turn)
        if inputs is None:
            return 0
        if inputs.truncated:
            log.warning("turn %s of %s exceeds the run log read limit; skill runs may be incomplete",
                        turn, conversation_id)
        slices = [item for item in slice_turns(inputs.events) if item.turn == turn]
        facts = project_turn(slices[-1], inputs.usage, inputs.artifact_count) if slices else ()
        skill_ids = await self._store.skill_ids(workspace_id, inputs.user_id, {fact.skill for fact in facts})
        run_id = run_id_of(conversation_id, turn)
        now = datetime.now(UTC)
        runs = [_turn_run(fact, workspace_id=workspace_id, run_id=run_id, user_id=inputs.user_id,
                          skill_id=skill_ids[fact.skill], now=now) for fact in facts if fact.skill in skill_ids]
        await self._store.replace_turn(workspace_id, run_id, runs)
        return len(runs)

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)


def _at(time_ms: int | None, fallback: datetime) -> datetime:
    return datetime.fromtimestamp(time_ms / 1000, UTC) if time_ms is not None else fallback


def _turn_run(fact: RunFact, *, workspace_id: str, run_id: str, user_id: str, skill_id: str,
              now: datetime) -> TurnRun:
    started = _at(fact.started_ms, now)
    return TurnRun(
        start=RunStart(workspace_id=workspace_id, run_id=run_id, session_id=fact.session_id, user_id=user_id,
                       skill_id=skill_id, skill_version=fact.version, selection_mode=fact.selection_mode),
        started_at=started, ended_at=max(_at(fact.ended_ms, now), started),
        finish=RunFinish(status=fact.status, total_tokens=fact.total_tokens, cost=float(fact.cost),
                         artifact_count=fact.artifact_count, failure_stage=fact.failure_stage,
                         failure_code=fact.failure_code),
        steps=tuple(StepRecord(step_key=step.tool_name or "tool", step_type="tool", seq=seq, status=step.status,
                               duration_ms=step.duration_ms, tool_name=step.tool_name or None,
                               approval_result=step.approval_result)
                    for seq, step in enumerate(fact.steps)))
