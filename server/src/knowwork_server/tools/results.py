"""结果来源：查询类工具（例如扩展的问数）把查出来的结果登记成带编号的结果，show_result 按编号展示它们的卡片
（tools/show_result.py）。展示只有 show_result 一个入口：原来问数自己有 show_result、开源另有 show_chart，
两个工具都说「给用户看」，模型把同一次查询的数据又抄进 show_chart 再画一张，第二张的「来源」还是模型自己写的，核对不了。

一个来源管一种编号（owns 按前缀认），给：
- results：本会话的结果（编号、展示过没有、卡片里那张结果表），show_result 拿它认出模型抄过来的数据；
- show：按编号交卡片，顺带记下「这个回答展示了它们」（例如问数按它判定题库）；编号对不上时 payload 是带候选的报错；
- modes：来源自己登记的回答方式里也用 show_result（只收编号，不收模型直接交的数据，每个数都要追得回查询）；
- guidance：show_result 的使用规则后面补的一段（例如问数「查出空结果的那次也要展示」）。
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from .registry import ToolContext

# 抄过来的数常被四舍五入（1234.5678 写成 1234.57）、单位换成「万」前也是原数：相对差千分之一、绝对差一分以内算同一个数
REL_TOLERANCE, ABS_TOLERANCE = 1e-3, 0.01


@dataclass(frozen=True)
class Result:
    id: str
    shown: bool  # 这段对话里已经展示过
    columns: list[dict[str, Any]] = field(default_factory=list)  # 卡片里那张结果表（协议见 tools/presentation.py）
    rows: list[list[Any]] = field(default_factory=list)


@dataclass(frozen=True)
class Shown:
    payload: dict[str, Any]  # 给模型的：status 是 ok 时 shown 每个编号一条；是 error 时是报错与候选
    cards: list[dict[str, Any]] = field(default_factory=list)


class ResultSource(Protocol):
    modes: frozenset[str]
    guidance: str

    def owns(self, result_id: str) -> bool: ...

    async def results(self, ctx: ToolContext) -> Sequence[Result]: ...

    async def show(self, ctx: ToolContext, ids: tuple[str, ...]) -> Shown: ...


class ResultSources:
    """扩展登记结果来源的地方（ApiContext.results）；show_result 展示时、交工具描述时现取。"""

    def __init__(self) -> None:
        self._items: list[ResultSource] = []

    def add(self, source: ResultSource) -> None:
        self._items.append(source)

    @property
    def items(self) -> tuple[ResultSource, ...]:
        return tuple(self._items)

    def owner(self, result_id: str) -> ResultSource | None:
        return next((source for source in self._items if source.owns(result_id)), None)

    @property
    def modes(self) -> frozenset[str]:
        return frozenset(mode for source in self._items for mode in source.modes)


def same_numbers(numbers: Sequence[Sequence[Any]], result: Result) -> bool:
    """模型交来的几列数值是不是这个结果里的数：行数一样，每一列都对得上结果里不同的一列（顺序不论，数按容差比），
    至少有一个数。只比数值：分类、时间常被换了写法（2026-06-01 写成 2026-06），比不上。"""
    if not numbers or not result.rows or any(len(column) != len(result.rows) for column in numbers):
        return False
    if not any(value is not None for column in numbers for value in column):
        return False
    candidates = [_sorted_numbers([row[i] if i < len(row) else None for row in result.rows])
                  for i in range(len(result.columns))]
    used: set[int] = set()
    for column in numbers:
        wanted = _sorted_numbers(column)
        hit = next((i for i, values in enumerate(candidates)
                    if i not in used and values is not None and _close(wanted, values)), None)
        if wanted is None or hit is None:
            return False
        used.add(hit)
    return True


def _sorted_numbers(values: Sequence[Any]) -> list[float | None] | None:
    """一列都是数（或空）就排好序交回；有一个不是数就不是数值列（None）。空值排在最前。"""
    numbers: list[float | None] = []
    for value in values:
        if value is None:
            numbers.append(None)
            continue
        number = _number(value)
        if number is None:
            return None
        numbers.append(number)
    return sorted(numbers, key=lambda n: (n is not None, n or 0.0))


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, str):  # 卡片里的小数是文字（扩展把 Decimal 换成字符串）
        try:
            number = Decimal(value.strip())
        except (InvalidOperation, ValueError):
            return None
        return float(number) if number.is_finite() else None
    return None


def _close(left: list[float | None], right: list[float | None]) -> bool:
    return len(left) == len(right) and all(
        a is None and b is None or (a is not None and b is not None
                                    and math.isclose(a, b, rel_tol=REL_TOLERANCE, abs_tol=ABS_TOLERANCE))
        for a, b in zip(left, right, strict=True))
