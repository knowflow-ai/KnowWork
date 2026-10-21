"""技能运行事实的查询（管理端「技能 → 最近运行」）：某个空间技能按版本的汇总，以及最近的每一次运行。

运行事实里有谁在用、花了多少，和「运行记录」同一口径，只给空间管理员看（路由上判）。
对话里的运行 run_id 是「会话:轮」（projector.run_id_of），拆出来给前端打开那一轮的运行详情；别的来源拆不出就留空。
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..access.rls import set_workspace
from ..conversations.models import Conversation
from .errors import SkillNotFound
from .models import Skill, SkillRun, SkillStepRun

MAX_LIMIT = 100


@dataclass(frozen=True)
class RunRow:
    id: str
    version: str
    selection_mode: str
    status: str
    failure_stage: str | None
    failure_code: str | None
    user_id: str
    started_at: datetime
    duration_ms: int | None
    total_tokens: int
    cost: str | None
    artifact_count: int
    steps: int
    failed_steps: int
    conversation_id: str | None
    turn: int | None
    title: str

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "version": self.version, "selectionMode": self.selection_mode, "status": self.status,
                "failureStage": self.failure_stage, "failureCode": self.failure_code, "userId": self.user_id,
                "startedAt": self.started_at.isoformat(), "durationMs": self.duration_ms,
                "totalTokens": self.total_tokens, "cost": self.cost, "artifactCount": self.artifact_count,
                "steps": self.steps, "failedSteps": self.failed_steps, "conversationId": self.conversation_id,
                "turn": self.turn, "title": self.title}


@dataclass(frozen=True)
class RunPage:
    items: tuple[RunRow, ...]
    next: str | None  # 下一页游标；没有更多为空


@dataclass(frozen=True)
class VersionStats:
    version: str
    runs: int
    succeeded: int
    failed: int
    cancelled: int
    avg_duration_ms: int | None
    avg_tokens: int
    top_failure_stage: str | None
    last_run_at: datetime

    def to_json(self) -> dict[str, Any]:
        return {"version": self.version, "runs": self.runs, "succeeded": self.succeeded, "failed": self.failed,
                "cancelled": self.cancelled, "avgDurationMs": self.avg_duration_ms, "avgTokens": self.avg_tokens,
                "topFailureStage": self.top_failure_stage, "lastRunAt": self.last_run_at.isoformat()}


@dataclass(frozen=True)
class RunFilter:
    since: datetime
    status: str | None = None
    version: str | None = None


def encode_cursor(row: RunRow) -> str:
    return f"{row.started_at.isoformat()}|{row.id}"


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    """游标格式错误抛 ValueError，路由翻成 400。"""
    at, separator, run_id = cursor.partition("|")
    if not separator or not run_id:
        raise ValueError("游标格式错误")
    return datetime.fromisoformat(at), run_id


def _turn_of(run_id: str) -> tuple[str | None, int | None]:
    conversation, separator, turn = run_id.rpartition(":")
    return (conversation, int(turn)) if separator and conversation and turn.isdigit() else (None, None)


def _ms(start: datetime, end: datetime | None) -> int | None:
    return max(int((end - start).total_seconds() * 1000), 0) if end is not None else None


def _money(value: Decimal | None) -> str | None:
    return None if value is None else format(Decimal(value).normalize(), "f")


class SkillRunQuery:
    def __init__(self, sessionmaker: async_sessionmaker) -> None:
        self._sessionmaker = sessionmaker

    async def recent(self, workspace_id: str, skill_id: str, filters: RunFilter, *, before: str | None = None,
                     limit: int = 20) -> RunPage:
        limit = min(max(limit, 1), MAX_LIMIT)
        steps = (select(SkillStepRun.skill_run_id, func.count().label("steps"),
                        func.count().filter(SkillStepRun.status == "failed").label("failed"))
                 .where(SkillStepRun.workspace_id == workspace_id).group_by(SkillStepRun.skill_run_id).subquery())
        statement = (select(SkillRun, steps.c.steps, steps.c.failed)
                     .outerjoin(steps, steps.c.skill_run_id == SkillRun.id)
                     .where(*self._scope(workspace_id, skill_id, filters))
                     .order_by(SkillRun.started_at.desc(), SkillRun.id.desc()).limit(limit + 1))
        if before is not None:
            statement = statement.where(tuple_(SkillRun.started_at, SkillRun.id) < tuple_(*decode_cursor(before)))
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            await self._ensure_skill(session, workspace_id, skill_id)
            rows = (await session.execute(statement)).all()
            turns = {row.SkillRun.id: _turn_of(row.SkillRun.run_id) for row in rows[:limit]}
            ids = {conversation for conversation, _ in turns.values() if conversation is not None}
            titles = dict((await session.execute(select(Conversation.id, Conversation.title)
                                                 .where(Conversation.id.in_(ids)))).all()) if ids else {}
        items = tuple(_row(row.SkillRun, row.steps or 0, row.failed or 0, *turns[row.SkillRun.id], titles)
                      for row in rows[:limit])
        return RunPage(items=items, next=encode_cursor(items[-1]) if len(rows) > limit else None)

    async def summary(self, workspace_id: str, skill_id: str, since: datetime) -> tuple[VersionStats, ...]:
        """按版本汇总，最近用过的版本在前。平均耗时只算有结束时间的运行。"""
        scope = self._scope(workspace_id, skill_id, RunFilter(since=since))
        counted = (select(SkillRun.skill_version, func.count(),
                          func.count().filter(SkillRun.status == "succeeded"),
                          func.count().filter(SkillRun.status == "failed"),
                          func.count().filter(SkillRun.status == "cancelled"),
                          func.avg(func.extract("epoch", SkillRun.ended_at - SkillRun.started_at) * 1000),
                          func.avg(SkillRun.total_tokens), func.max(SkillRun.started_at))
                   .where(*scope).group_by(SkillRun.skill_version).order_by(func.max(SkillRun.started_at).desc()))
        stages = (select(SkillRun.skill_version, SkillRun.failure_stage, func.count())
                  .where(*scope, SkillRun.status == "failed", SkillRun.failure_stage.is_not(None))
                  .group_by(SkillRun.skill_version, SkillRun.failure_stage))
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            await self._ensure_skill(session, workspace_id, skill_id)
            totals = (await session.execute(counted)).all()
            failures = (await session.execute(stages)).all()
        top = _top_stages(failures)
        return tuple(VersionStats(version=version, runs=runs, succeeded=ok, failed=bad, cancelled=cancelled,
                                  avg_duration_ms=round(avg_ms) if avg_ms is not None else None,
                                  avg_tokens=round(avg_tokens or 0), top_failure_stage=top.get(version),
                                  last_run_at=last)
                     for version, runs, ok, bad, cancelled, avg_ms, avg_tokens, last in totals)

    @staticmethod
    def _scope(workspace_id: str, skill_id: str, filters: RunFilter) -> tuple:
        conditions = [SkillRun.workspace_id == workspace_id, SkillRun.skill_id == skill_id,
                      SkillRun.started_at >= filters.since]
        if filters.status is not None:
            conditions.append(SkillRun.status == filters.status)
        if filters.version is not None:
            conditions.append(SkillRun.skill_version == filters.version)
        return tuple(conditions)

    @staticmethod
    async def _ensure_skill(session, workspace_id: str, skill_id: str) -> None:
        """只看本空间的空间技能；个人技能的运行不在管理端露出。"""
        found = await session.scalar(select(Skill.id).where(Skill.id == skill_id, Skill.workspace_id == workspace_id,
                                                            Skill.scope == "workspace"))
        if found is None:
            raise SkillNotFound("技能不存在")


def _row(run: SkillRun, steps: int, failed: int, conversation: str | None, turn: int | None,
         titles: dict[str, str]) -> RunRow:
    return RunRow(id=run.id, version=run.skill_version, selection_mode=run.selection_mode, status=run.status,
                  failure_stage=run.failure_stage, failure_code=run.failure_code, user_id=run.user_id,
                  started_at=run.started_at, duration_ms=_ms(run.started_at, run.ended_at),
                  total_tokens=run.total_tokens, cost=_money(run.cost), artifact_count=run.artifact_count,
                  steps=steps, failed_steps=failed, conversation_id=conversation, turn=turn,
                  title=titles.get(conversation or "", "") or "")


def _top_stages(rows) -> dict[str, str]:
    """每个版本出现最多的失败阶段；次数相同取阶段名靠前的，结果稳定。"""
    ordered = sorted(rows, key=lambda row: (row[0], -row[2], row[1]))
    # 倒着放进字典，同一版本排在最前的那条最后写入、留下来
    return {version: stage for version, stage, _ in reversed(ordered)}
