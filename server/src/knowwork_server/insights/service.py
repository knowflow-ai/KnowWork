"""问答大盘：智能体跑得怎么样、哪里出问题。只给空间所有者和管理员（workspace:edit，同运行记录、审计）：
数字都能点到运行记录里具体是哪几轮，那里有提问和提问人。

- 四个数：提问轮数、提问人数、完成率（完成 / 已结束）、费用，各和上一段同样长的时间比；
- 每天的提问与完成率；
- 需要关注：失败、取消按结束原因分，被点踩按分类分，检索了知识库却没引用的轮数；
- 工具、知识库两个维度：用到几轮、失败或没用上几轮。
按统计时区（和「用量」同一个）分天。数据都来自每轮汇总（conversations.summaries），费用来自用量表。
"""

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Protocol
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import async_sessionmaker

from ..access.rls import set_workspace
from . import queries

log = logging.getLogger(__name__)

RANGES = (7, 30, 90)
WORKSPACE = "workspace"

BaseNames = Callable[[str, str], Awaitable[Mapping[str, str]]]  # (空间, 查看者) → 知识库全局 id → 名字


class AccessChecker(Protocol):
    def check(self, user_id: str, workspace_id: str, resource: str, action: str) -> bool: ...


class Forbidden(Exception):
    pass


class Invalid(Exception):
    pass


@dataclass(frozen=True)
class Totals:
    turns: int
    askers: int
    completed: int
    failed: int
    cancelled: int
    liked: int
    disliked: int
    searched: int = 0  # 答完了、检索过知识库的轮数（「检索了没引用」占比的分母）
    cost: str = "0"
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class Day:
    date: str
    turns: int
    completed: int
    ended: int  # 已结束的（完成 + 失败 + 取消），完成率的分母


@dataclass(frozen=True)
class Count:
    key: str     # 结束原因、点踩分类、工具名或知识库全局 id
    count: int
    status: str = ""  # 结束原因所属的状态：failed | cancelled


@dataclass(frozen=True)
class ToolUse:
    name: str
    turns: int
    failed: int


@dataclass(frozen=True)
class BaseUse:
    id: str      # 知识库全局 id，点下去按它筛运行记录
    name: str
    turns: int   # 检索到它的轮数
    cited: int   # 引用了它的轮数
    unused: int  # 答完了却没引用它的轮数
    disliked: int


@dataclass(frozen=True)
class Overview:
    start: date
    end: date
    since: datetime  # 统计用的起止时刻 [since, until)：点下去的运行记录按它筛，两边数得上
    until: datetime
    totals: Totals
    previous: Totals  # 上一段同样长的时间
    days: tuple[Day, ...]
    end_reasons: tuple[Count, ...]
    dislikes: tuple[Count, ...]
    uncited: int
    tools: tuple[ToolUse, ...]
    bases: tuple[BaseUse, ...]


def window(days: int, timezone: str, today: date | None = None) -> tuple[date, date, datetime, datetime]:
    """最近 days 天（含今天，按统计时区）：起止日期与对应的时刻 [start, end)。"""
    zone = ZoneInfo(timezone)
    last = today or datetime.now(zone).date()
    first = last - timedelta(days=days - 1)
    return (first, last, datetime.combine(first, time.min, zone),
            datetime.combine(last + timedelta(days=1), time.min, zone))


def _money(value) -> str:
    return format(Decimal(value or 0).normalize(), "f")


class InsightsService:
    def __init__(self, sessionmaker: async_sessionmaker, access: AccessChecker, *, timezone: str,
                 base_names: BaseNames | None = None) -> None:
        self._sessionmaker = sessionmaker
        self._access = access
        self._timezone = timezone
        self._base_names = base_names

    async def overview(self, actor: str, workspace_id: str, days: int, *, today: date | None = None) -> Overview:
        if not self._access.check(actor, workspace_id, WORKSPACE, "edit"):
            raise Forbidden()
        if days not in RANGES:
            raise Invalid(f"时间范围只能是最近 {'、'.join(map(str, RANGES))} 天")
        first, last, start, end = window(days, self._timezone, today)
        before = start - (end - start)
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            now = await self._totals(session, workspace_id, start, end)
            earlier = await self._totals(session, workspace_id, before, start)
            daily = await queries.by_day(session, workspace_id, start, end, self._timezone)
            reasons = await queries.end_reasons(session, workspace_id, start, end)
            dislikes = await queries.dislike_categories(session, workspace_id, start, end)
            uncited = await queries.uncited(session, workspace_id, start, end)
            tools = await queries.tools(session, workspace_id, start, end)
            bases = await queries.bases(session, workspace_id, start, end)
        names = await self._names(workspace_id, actor) if bases else {}
        return Overview(
            start=first, end=last, since=start, until=end, totals=now, previous=earlier,
            days=tuple(_day(day, daily.get(day.isoformat(), {})) for day in (first + timedelta(days=n)
                                                                              for n in range(days))),
            end_reasons=tuple(Count(key=reason, count=count, status=status) for status, reason, count in reasons),
            dislikes=tuple(Count(key=category, count=count) for category, count in dislikes), uncited=uncited,
            tools=tuple(ToolUse(name=name, turns=turns, failed=failed) for name, turns, failed in tools),
            bases=tuple(BaseUse(id=base, name=names.get(base, base), turns=turns, cited=cited, unused=unused,
                                disliked=disliked) for base, turns, cited, unused, disliked in bases))

    async def _totals(self, session, workspace_id: str, start: datetime, end: datetime) -> Totals:
        counts = await queries.totals(session, workspace_id, start, end)
        cost, input_tokens, output_tokens = await queries.spend(session, workspace_id, start, end)
        return Totals(**counts, cost=_money(cost), input_tokens=input_tokens, output_tokens=output_tokens)

    async def _names(self, workspace_id: str, actor: str) -> Mapping[str, str]:
        """知识库的名字按查看者能查的列；列不出来（知识源连不上）就显示全局 id，不挡大盘。"""
        if self._base_names is None:
            return {}
        try:
            return await self._base_names(workspace_id, actor)
        except Exception:
            log.warning("knowledge base names unavailable for insights", exc_info=True)
            return {}


def _day(day: date, found: Mapping[str, int]) -> Day:
    """一天的数；没有提问的日子全是 0。"""
    completed = found.get("completed", 0)
    return Day(date=day.isoformat(), turns=found.get("turns", 0), completed=completed,
               ended=completed + found.get("failed", 0) + found.get("cancelled", 0))
