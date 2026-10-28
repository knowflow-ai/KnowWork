"""UsageRecorder：写用量记录、同事务按日汇总，按用户与按模型查询（只读汇总表）。

- 同一会话同一步（turn, step）只记一次：宿主重放事件不会重复计量，汇总也只在真正插入时累加；
- 费用按记录时模型的单价估算（每百万 token），模型没配价格则为空；
- 「哪一天」按配置的时区切分，默认 Asia/Shanghai。
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..access.members import PLATFORM_WORKSPACE_ID
from ..access.rls import set_workspace
from .core import check_id
from .errors import Invalid
from .tables import Model, UsageDaily, UsageRecord
from .usage import UsageEntry

DEFAULT_TIMEZONE = "Asia/Shanghai"
MAX_RANGE_DAYS = 366
_MILLION = Decimal(1_000_000)


class GroupBy(StrEnum):
    USER = "user"
    MODEL = "model"
    DAY = "day"


@dataclass(frozen=True)
class UsageSummary:
    key: str
    calls: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost: Decimal


def estimate_cost(entry: UsageEntry, input_price: Decimal | None, output_price: Decimal | None) -> Decimal | None:
    if input_price is None and output_price is None:
        return None
    total = Decimal(entry.input_tokens) * (input_price or 0) + Decimal(entry.output_tokens) * (output_price or 0)
    return (total / _MILLION).quantize(Decimal("0.000001"))


@dataclass(frozen=True)
class StepUsage:
    """一步模型调用的用量（运行调试视图按 turn、step 对上会话日志里的步骤）。"""

    turn: int
    step: int
    model_alias: str
    input_tokens: int
    output_tokens: int
    cost: Decimal | None
    duration_ms: int | None
    status: str


@dataclass(frozen=True)
class TurnUsage:
    """一轮的用量合计（管理端「运行记录」列表用）。"""

    calls: int
    input_tokens: int
    output_tokens: int
    cost: Decimal | None


class UsageRecorder:
    def __init__(self, sessionmaker: async_sessionmaker, *, timezone: str = DEFAULT_TIMEZONE) -> None:
        self._sessionmaker = sessionmaker
        self._tz = ZoneInfo(timezone)

    async def record(self, entry: UsageEntry) -> bool:
        """写一条用量；返回是否新插入（重复的 turn/step 返回 False）。"""
        check_id(entry.workspace_id, "workspace_id")
        if entry.occurred_at.tzinfo is None:
            raise Invalid("occurred_at 必须带时区")
        day = entry.occurred_at.astimezone(self._tz).date()
        async with self._sessionmaker() as session, session.begin():
            # 价格从平台配置取（模型全平台共用），用量记在调用者自己的空间：同一事务里先后切两次行级安全变量
            await set_workspace(session, PLATFORM_WORKSPACE_ID)
            model = (await session.execute(select(Model.id, Model.input_price_per_mtok, Model.output_price_per_mtok)
                                           .where(Model.alias == entry.model_alias))).first()
            model_id, cost = (None, None) if model is None else (model[0], estimate_cost(entry, model[1], model[2]))
            await set_workspace(session, entry.workspace_id)
            inserted = await session.scalar(self._insert_record(entry, model_id, cost, day))
            if inserted is None:
                return False
            await session.execute(self._upsert_daily(entry, cost, day))
        return True

    @staticmethod
    def _insert_record(entry: UsageEntry, model_id: str | None, cost: Decimal | None, day: date):
        return (insert(UsageRecord).values(
            workspace_id=entry.workspace_id, user_id=entry.user_id, conversation_id=entry.conversation_id,
            turn=entry.turn, step=entry.step, model_alias=entry.model_alias, model_id=model_id,
            input_tokens=entry.input_tokens, output_tokens=entry.output_tokens, total_tokens=entry.total_tokens,
            cache_read_tokens=entry.cache_read_tokens, reasoning_tokens=entry.reasoning_tokens,
            duration_ms=entry.duration_ms, cost=cost, status=entry.status, occurred_at=entry.occurred_at, day=day)
            .on_conflict_do_nothing(constraint="uq_usage_step").returning(UsageRecord.id))

    @staticmethod
    def _upsert_daily(entry: UsageEntry, cost: Decimal | None, day: date):
        stmt = insert(UsageDaily).values(
            workspace_id=entry.workspace_id, day=day, user_id=entry.user_id, model_alias=entry.model_alias, calls=1,
            input_tokens=entry.input_tokens, output_tokens=entry.output_tokens, total_tokens=entry.total_tokens,
            cost=cost or Decimal(0))
        excluded = stmt.excluded
        return stmt.on_conflict_do_update(
            index_elements=["workspace_id", "day", "user_id", "model_alias"],
            set_={"calls": UsageDaily.calls + 1,
                  "input_tokens": UsageDaily.input_tokens + excluded.input_tokens,
                  "output_tokens": UsageDaily.output_tokens + excluded.output_tokens,
                  "total_tokens": UsageDaily.total_tokens + excluded.total_tokens,
                  "cost": UsageDaily.cost + excluded.cost})

    async def summary(self, workspace_id: str, start: date, end: date, group_by: GroupBy, *,
                      user_id: str | None = None) -> tuple[UsageSummary, ...]:
        """[start, end] 闭区间内按用户、模型或天汇总，按 total_tokens 降序（按天时按日期升序）。"""
        check_id(workspace_id, "workspace_id")
        if end < start or (end - start).days >= MAX_RANGE_DAYS:
            raise Invalid(f"时间范围须为 1 到 {MAX_RANGE_DAYS} 天")
        column = {GroupBy.USER: UsageDaily.user_id, GroupBy.MODEL: UsageDaily.model_alias,
                  GroupBy.DAY: UsageDaily.day}[GroupBy(group_by)]
        stmt = (select(column, func.sum(UsageDaily.calls), func.sum(UsageDaily.input_tokens),
                       func.sum(UsageDaily.output_tokens), func.sum(UsageDaily.total_tokens),
                       func.sum(UsageDaily.cost))
                .where(UsageDaily.day >= start, UsageDaily.day <= end).group_by(column))
        if user_id is not None:
            stmt = stmt.where(UsageDaily.user_id == check_id(user_id, "user_id"))
        stmt = stmt.order_by(column) if group_by == GroupBy.DAY else stmt.order_by(func.sum(
            UsageDaily.total_tokens).desc(), column)
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            rows = (await session.execute(stmt)).all()
        return tuple(_summary(row) for row in rows)

    async def steps(self, workspace_id: str, conversation_id: str) -> tuple[StepUsage, ...]:
        """一个会话里每一步模型调用的用量，按 turn、step 排序。"""
        check_id(workspace_id, "workspace_id")
        stmt = (select(UsageRecord.turn, UsageRecord.step, UsageRecord.model_alias, UsageRecord.input_tokens,
                       UsageRecord.output_tokens, UsageRecord.cost, UsageRecord.duration_ms, UsageRecord.status)
                .where(UsageRecord.conversation_id == conversation_id)
                .order_by(UsageRecord.turn, UsageRecord.step))
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            rows = (await session.execute(stmt)).all()
        return tuple(StepUsage(*row) for row in rows)

    async def turn_totals(self, workspace_id: str,
                          conversation_ids: tuple[str, ...]) -> dict[tuple[str, int], TurnUsage]:
        """这些会话每一轮的模型调用次数、token 与费用合计。没配价格的模型不计费用。"""
        check_id(workspace_id, "workspace_id")
        if not conversation_ids:
            return {}
        stmt = (select(UsageRecord.conversation_id, UsageRecord.turn, func.count(), func.sum(UsageRecord.input_tokens),
                       func.sum(UsageRecord.output_tokens), func.sum(UsageRecord.cost))
                .where(UsageRecord.conversation_id.in_(conversation_ids))
                .group_by(UsageRecord.conversation_id, UsageRecord.turn))
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            rows = (await session.execute(stmt)).all()
        return {(cid, turn): TurnUsage(calls=int(calls), input_tokens=int(inputs or 0), output_tokens=int(outputs or 0),
                                       cost=cost) for cid, turn, calls, inputs, outputs, cost in rows}


def _summary(row) -> UsageSummary:
    key, calls, inputs, outputs, total, cost = row
    return UsageSummary(key=str(key), calls=int(calls or 0), input_tokens=int(inputs or 0),
                        output_tokens=int(outputs or 0), total_tokens=int(total or 0), cost=Decimal(cost or 0))
