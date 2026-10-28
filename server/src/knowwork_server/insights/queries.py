"""问答大盘的统计查询：都在每轮汇总（run_summary）上算，赞踩连 answer_feedback（run_id 是 run-<轮次>），
费用取用量表。按开始时间落在时间窗里的轮次算；都在调用方声明了工作空间的会话里跑，查询里也按空间过滤
（同运行记录的列表，不只靠行级安全）。"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, and_, bindparam, distinct, false, func, literal_column, not_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..conversations.models import AnswerFeedback, RunSummary
from ..models.tables import UsageRecord

S, F = RunSummary, AnswerFeedback
TOTAL_COLUMNS = ("turns", "askers", "completed", "failed", "cancelled", "liked", "disliked", "searched")
MAX_ROWS = 20


def _knowledge(key: str):
    return S.facts["knowledge"][key]


def _in(workspace_id: str, start: datetime, end: datetime) -> tuple:
    return S.workspace_id == workspace_id, S.started_at >= start, S.started_at < end


def _window(workspace_id: str, start: datetime, end: datetime):
    """时间窗里的轮次，连上它的赞踩。"""
    feedback = and_(F.conversation_id == S.conversation_id, F.run_id == func.concat("run-", S.turn))
    return select().select_from(S).outerjoin(F, feedback).where(*_in(workspace_id, start, end))


def _measures():
    """searched 是答完了、检索过知识库的轮数（「检索了没引用」的分母）。"""
    return (func.count(), func.count(distinct(S.user_id)), func.count().filter(S.status == "completed"),
            func.count().filter(S.status == "failed"), func.count().filter(S.status == "cancelled"),
            func.count().filter(F.rating == "like"), func.count().filter(F.rating == "dislike"),
            func.count().filter(S.status == "completed", _knowledge("searches").as_integer() > 0))


async def totals(session: AsyncSession, workspace_id: str, start: datetime, end: datetime) -> dict[str, int]:
    row = (await session.execute(_window(workspace_id, start, end).add_columns(*_measures()))).one()
    return dict(zip(TOTAL_COLUMNS, row, strict=True))


async def by_day(session: AsyncSession, workspace_id: str, start: datetime, end: datetime, timezone: str
                ) -> dict[str, dict[str, int]]:
    """按统计时区的日期分组；没有提问的日子不出现。时区按字面量写进语句：写成参数时，选择列和 GROUP BY 里是两个
    不同的参数，PostgreSQL 认为不是同一个表达式。"""
    day = func.date(func.timezone(bindparam("zone", timezone, literal_execute=True), S.started_at))
    rows = (await session.execute(_window(workspace_id, start, end).add_columns(day, *_measures()).group_by(day))).all()
    return {row[0].isoformat(): dict(zip(TOTAL_COLUMNS, row[1:], strict=True)) for row in rows}


async def end_reasons(session: AsyncSession, workspace_id: str, start: datetime, end: datetime) -> Sequence[Any]:
    """失败、取消的轮次按宿主给的结束原因分（error、blocked、aborted……），多的在前。"""
    reason = func.coalesce(S.end_reason, "")
    count = func.count()
    return (await session.execute(
        select(S.status, reason, count).where(*_in(workspace_id, start, end),
                                              S.status.in_(("failed", "cancelled")))
        .group_by(S.status, reason).order_by(count.desc()))).all()


async def dislike_categories(session: AsyncSession, workspace_id: str, start: datetime, end: datetime) -> Sequence[Any]:
    """被点踩的轮次按用户选的分类分（没选分类的记空），多的在前。"""
    category = func.coalesce(F.category, "")
    count = func.count()
    return (await session.execute(
        _window(workspace_id, start, end).add_columns(category, count).where(F.rating == "dislike")
        .group_by(category).order_by(count.desc()))).all()


async def uncited(session: AsyncSession, workspace_id: str, start: datetime, end: datetime) -> int:
    """检索了知识库、正常答完却没引用的轮数（同运行记录的 uncited 信号）。"""
    return await session.scalar(select(func.count()).select_from(S).where(
        *_in(workspace_id, start, end), S.status == "completed",
        _knowledge("searches").as_integer() > 0, _knowledge("cited").as_integer() == 0)) or 0


async def tools(session: AsyncSession, workspace_id: str, start: datetime, end: datetime) -> Sequence[Any]:
    """每个工具用到的轮数、其中失败的轮数，用得多的在前。"""
    name = func.jsonb_array_elements_text(S.tools).table_valued("value").alias("tool")
    turns = func.count()
    return (await session.execute(
        select(name.c.value, turns, func.count().filter(S.status == "failed"))
        .select_from(S).join(name, literal_column("true"))
        .where(*_in(workspace_id, start, end))
        .group_by(name.c.value).order_by(turns.desc(), name.c.value).limit(MAX_ROWS))).all()


async def bases(session: AsyncSession, workspace_id: str, start: datetime, end: datetime) -> Sequence[Any]:
    """每个知识库检索到它的轮数、引用了它的轮数、检索到却没用上（答完了、没引用它）的轮数、被点踩的轮数。"""
    base = func.jsonb_array_elements_text(_knowledge("bases")).table_valued("value").alias("base")
    used = func.coalesce(_knowledge("cited_bases").op("?", return_type=Boolean)(base.c.value), false(),
                         type_=Boolean)
    turns = func.count()
    return (await session.execute(
        _window(workspace_id, start, end).add_columns(base.c.value, turns, func.count().filter(used),
                                        func.count().filter(S.status == "completed", not_(used)),
                                        func.count().filter(F.rating == "dislike"))
        .join(base, literal_column("true"))
        .group_by(base.c.value).order_by(turns.desc(), base.c.value).limit(MAX_ROWS))).all()


async def spend(session: AsyncSession, workspace_id: str, start: datetime, end: datetime) -> tuple[Any, int, int]:
    """模型费用与 token（同「用量」页，按调用发生的时间算）；没填单价的模型费用记为 0。"""
    row = (await session.execute(select(
        func.coalesce(func.sum(UsageRecord.cost), 0), func.coalesce(func.sum(UsageRecord.input_tokens), 0),
        func.coalesce(func.sum(UsageRecord.output_tokens), 0))
        .where(UsageRecord.workspace_id == workspace_id, UsageRecord.occurred_at >= start,
               UsageRecord.occurred_at < end))).one()
    return row[0], int(row[1]), int(row[2])

