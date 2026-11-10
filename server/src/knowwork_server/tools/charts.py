"""结果表怎么画（卡片协议 tools/presentation.py 里表格的 chart）：照成熟问数产品按结果的形状推断，只看列是时间、分类
还是数值、有几行、分类有几个，不调模型、不让 Agent 选。同样的结果总是同样的图，用户在页面上可以换成兼容的图形或切回表。
扩展的问数结果卡片和智能体的 show_chart（tools/show_chart.py）都用这一份，两边画出来一致（2026-11-09 从问数挪过来）。

调用方先给每列定角色：time 时间、category 分类、measure 数值，None 不画（例如编号）。问数按列在查询里的角色定，
show_chart 按模型标的定。列本身给 name、type（text / number / date…：文字的分类才可能画饼，整列写成年月的文字当时间），
数值列可以带 unit（几列数值能不能画在一根轴上，先看单位）。
"""

import re
from collections.abc import Collection, Mapping, Sequence
from typing import Any

PIE_MAX, BAR_MAX, SERIES_MAX = 7, 100, 8  # 几块以内画饼、几类以内画柱状、几条线以内按分组分线
GROUPED_MAX = 50  # 按组分开的柱子，横轴最多几类（同成熟问数产品的分组柱状门槛）
TOGETHER_RATIO = 10  # 没写单位的几列数值，量级差不到这么多倍才画在一根轴上（差多了小的那条贴着横轴，看不出起伏）
# 写成文字的年月、年季（2026-01、2026/01/05、2026年1月、2026 Q1、2026年第一季度）：写 SQL 按月汇总时月份常出成文字。
# 要带月或季，只有四位数的不算，编号、代码也长那样（前端 chartOption 的 TIME_LIKE 只管柱子朝向，可以宽一些）
TIME_TEXT = re.compile(r"^\d{4}(?:[-/.]\d{1,2}(?:[-/.]\d{1,2})?|\s*年\s*\d{1,2}\s*月|\s*年?\s*(?:[Qq][1-4]|第?[一二三四1-4]季度))$")

Column = Mapping[str, Any]


def chart_by_shape(columns: Sequence[Column], roles: Sequence[str | None], rows: Sequence[Sequence[Any]], *,
                   additive: Collection[str] = frozenset()) -> dict[str, Any] | None:
    """按结果的形状定怎么画（只看给的这几行）。additive：能相加的数值列（求和、计数），画饼（占比）才有意义。
    - 一列时间加数值、每个时间一行：折线，横轴是时间；
    - 一列时间、一列分类加数值，每个时间 × 分类一行：按分类分成几条折线（分类太多就不画）；
    - 一列分类加数值：柱状；文字的分类、只有一个能相加的数值、几块以内、分类不重复、都不为负时画饼；
    - 两列分类加数值（按地区、客户等级分）：分组柱状，取值少的那列分组、另一列当横轴，每个横轴 × 分组一行才画；
      成熟问数产品这里只取第一列当横轴，同一个地区会出现好几根柱子、分不清哪根是哪个等级；
    - 少于两行、没有数值、同一个时间好几行（一行一条记录的列表）、别的形状：不画。
    折线、柱状有几列数值时，能画在一根轴上的分成一组（groups，见 _together），界面同组的画在一张图里。"""
    if len(rows) < 2:
        return None
    timed = [_timed(role, column, i, rows) for i, (role, column) in enumerate(zip(roles, columns, strict=True))]
    names = [c["name"] for c in columns]
    measures = [names[i] for i, role in enumerate(timed) if role == "measure"]
    times = [i for i, role in enumerate(timed) if role == "time"]
    categories = [i for i, role in enumerate(timed) if role == "category"]
    if not measures:
        return None
    if len(times) == 1 and not categories:
        unique = len({_key(row[times[0]]) for row in rows}) == len(rows)
        return _together({"type": "line", "x": names[times[0]], "y": measures}, columns, rows) if unique else None
    if len(times) == 1 and len(categories) == 1:
        pairs = {(_key(row[times[0]]), _key(row[categories[0]])) for row in rows}
        groups = {_key(row[categories[0]]) for row in rows}
        if len(pairs) != len(rows) or len(groups) > SERIES_MAX:
            return None
        return {"type": "line", "x": names[times[0]], "y": measures, "series": names[categories[0]]}
    if not times and len(categories) == 1:
        count = len({_key(row[categories[0]]) for row in rows})
        if count > BAR_MAX:
            return None
        values = [row[names.index(measures[0])] for row in rows]
        # 年份、等级这类数字的分组是有先后的，按它分的营收画成占比不对：只有文字的分类才默认画饼
        textual = columns[categories[0]].get("type") == "text"
        pie = (textual and len(measures) == 1 and measures[0] in additive and count == len(rows) and count <= PIE_MAX
               and all(n is not None and n >= 0 for n in map(number, values)) and any((number(v) or 0) > 0 for v in values))
        drawn = {"type": "pie" if pie else "bar", "x": names[categories[0]], "y": measures}
        return drawn if pie else _together(drawn, columns, rows)
    if not times and len(categories) == 2:
        return _grouped(rows, names, categories, measures)
    return None


def number(value: Any) -> float | None:
    """卡片里的数：小数可能已经写成文字（问数把 Decimal 换成字符串），照样认。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _together(chart: dict[str, Any], columns: Sequence[Column], rows: Sequence[Sequence[Any]]) -> dict[str, Any]:
    """几列数值里能画在一根轴上的分成一组，按 y 的先后依次放进第一个和组里每列都合得来的组：
    两列都写了单位的按单位，一样才合得来（金额和单价量级再近也不放一起）；有没写单位的（写 SQL 查的列都没有）看量级，
    差不到 TOGETHER_RATIO 倍的合得来（营收和目标放一起，金额和单数分开）。分不出两列以上的组就不带 groups，界面一列一列切换。"""
    by_name = {c["name"]: c for c in columns}
    index = {c["name"]: i for i, c in enumerate(columns)}
    scale = {name: max((abs(n) for row in rows if (n := number(row[index[name]])) is not None), default=0.0)
             for name in chart["y"]}

    def fits(a: str, b: str) -> bool:
        units = by_name[a].get("unit"), by_name[b].get("unit")
        if all(units):
            return units[0] == units[1]
        small, large = sorted((scale[a], scale[b]))
        return small == 0 or large <= small * TOGETHER_RATIO

    groups: list[list[str]] = []
    for name in chart["y"]:
        home = next((i for i, group in enumerate(groups) if all(fits(name, other) for other in group)), None)
        groups = [*groups, [name]] if home is None else [g + [name] if i == home else g for i, g in enumerate(groups)]
    return {**chart, "groups": groups} if any(len(g) > 1 for g in groups) else chart


def _grouped(rows: Sequence[Sequence[Any]], names: list[str], categories: list[int],
             measures: list[str]) -> dict[str, Any] | None:
    counts = [len({_key(row[i]) for row in rows}) for i in categories]
    x, series = categories if counts[1] <= counts[0] else categories[::-1]
    pairs = {(_key(row[x]), _key(row[series])) for row in rows}
    if len(pairs) != len(rows) or min(counts) > SERIES_MAX or max(counts) > GROUPED_MAX:
        return None
    return {"type": "bar", "x": names[x], "y": measures, "series": names[series]}


def _key(value: Any) -> str:
    return "" if value is None else str(value)


def _timed(role: str | None, column: Column, index: int, rows: Sequence[Sequence[Any]]) -> str | None:
    """文字的分类列，整列（不算空）都写成年月、年季的，当时间。"""
    if role != "category" or column.get("type") != "text":
        return role
    values = [row[index] for row in rows if row[index] is not None]
    return "time" if values and all(isinstance(v, str) and TIME_TEXT.match(v.strip()) for v in values) else role
