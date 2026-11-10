"""展示结果（show_result）：回答下方的结果卡片只有这一个入口，前端同一个组件画（ECharts）。

两种输入：
- query_ids：查询类工具查出来的结果（扩展登记的结果来源，tools/results.py），卡片是查询时拼好的，口径、SQL 由服务端记下；
- tables：还没有查询编号的数据（附件、网页里的、自己算出来的），模型交汇总好的数据和每列是什么（时间、分类、数值），
  画什么图按形状推断（tools/charts.py，问数也用这一份），同样的数据总是同样的图；卡片照查询结果的格式：标题
  「数值 · 按分组」、一行灰字、底部一行出处（这里写「来源」，点开看明细和算数的代码）。

同一份数据只出一张卡片：tables 里的数值和本会话某次查询的结果对得上（模型抄过来的），就按那次查询展示，那次已经
展示过的不再出。只有智能体收 tables；扩展登记的回答方式（例如问数）只收编号，每个数都要追得回查询。

为什么不让模型画：沙箱里 matplotlib 出的是静态图片，样式和界面不一致，不能切换图形、切回表、悬停看数；让模型直接写
ECharts 配置也不行，配置里提示框的格式字符串按 HTML 渲染，附件、知识库里的注入就能变成页面上的脚本。只有要放进文件
（Word、PPT、Excel）的图才在沙箱里画。
"""

import json
from dataclasses import dataclass, replace
from typing import Any

from .charts import chart_by_shape
from .presentation import MAX_COLUMNS, MAX_ROWS, VERSION, normalize
from .registry import AGENT_ONLY, BuiltinTool, ToolContext, ToolFailure, ToolOutput, ToolSpec
from .results import Result, ResultSource, ResultSources, same_numbers

NAME = "show_result"
LABEL = "来源"
KINDS = ("time", "category", "number", "text")
ROLES = {"time": "time", "category": "category", "number": "measure", "text": None}
CARD_KINDS = {"time": "date", "number": "number"}  # 界面上的列类型只管对齐与横轴是不是时间，其余都是 text
CHART_NAMES = {"line": "折线图", "bar": "柱状图", "pie": "饼图"}
INLINE_MODES = AGENT_ONLY  # 收模型直接交的数据的回答方式；扩展的方式每个数都要追得回查询
MAX_IDS = 5
MAX_TABLES = 5
MAX_NAME = 100
MAX_DETAILS = 20
DRAWABLE = ("画得成图的形状（至少两行）：一列时间加数值，每个时间一行（折线）；一列时间、一列分类加数值，每种组合一行"
            "（按分类分线，分类最多 8 个）；一列分类加数值（柱状；标了能相加的一个数值、7 块以内、分类不重复、都不为负时画饼）；"
            "两列分类加数值，每种组合一行（分组柱状）。")
INLINE_ONLY_IN_AGENT = "这种回答方式只展示查询结果：不收 tables，按 query_ids 展示查询工具查出来的结果"

CELL = {"oneOf": [{"type": "string"}, {"type": "number"}, {"type": "null"}]}
TABLE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "卡片标题，写成「数值 · 按分组」，例如「销售额 · 按区域」；不写就按列名拼"},
        "subtitle": {"type": "string", "description": "标题下面一行灰字，写时间范围，例如「2026-01-01 至 2026-06-30」；没有就不写"},
        "columns": {
            "type": "array",
            "description": f"表头，和每行的值按顺序一一对应，最多 {MAX_COLUMNS} 列，至少一列数值",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "列名，界面上直接显示，用中文写清是什么，例如「销售额」「区域」"},
                    "kind": {"type": "string", "enum": list(KINDS),
                             "description": "time 时间（日期、年月、季度、年份）；category 分类（区域、产品、渠道）；"
                                            "number 数值；text 别的文字（编号、备注，不画）"},
                    "unit": {"type": "string", "description": "数值的单位，例如「万元」「%」「单」；几列数值按单位分组画"},
                    "additive": {"type": "boolean", "description": "数值列各行加起来有意义（销售额、人数）时写 true，"
                                                                   "只有这样才可能画成饼图（占比）；平均值、比率不写"},
                },
                "required": ["name", "kind"],
                "additionalProperties": False,
            },
        },
        "rows": {"type": "array", "items": {"type": "array", "items": CELL},
                 "description": f"数据，每行一个数组，最多 {MAX_ROWS} 行；数值写成数字（不带单位、千分位、百分号），缺的写 null"},
        "source": {"type": "string", "description": "数据从哪来、怎么算的，一行，显示在卡片底部，例如「附件 2025年销售.xlsx 的“月度”"
                                                    "表，按区域求和」"},
        "details": {
            "type": "array",
            "description": f"点开出处看的明细（可选，最多 {MAX_DETAILS} 项），例如 {{\"name\": \"文件\", \"value\": \"2025年销售.xlsx\"}}",
            "items": {"type": "object", "properties": {"name": {"type": "string"}, "value": {"type": "string"}},
                      "required": ["name", "value"], "additionalProperties": False},
        },
        "code": {"type": "string", "description": "算出这些数的代码（可选，例如汇总用的 Python），点开出处能看、能复制"},
        "code_language": {"type": "string", "description": "code 的语言，例如 python"},
    },
    "required": ["columns", "rows", "source"],
    "additionalProperties": False,
}
SCHEMA = {
    "type": "object",
    "properties": {
        "query_ids": {"type": "array", "items": {"type": "string"},
                      "description": f"查询工具成功返回的 query_id（本会话的），最多 {MAX_IDS} 个"},
        "tables": {"type": "array", "items": TABLE_SCHEMA,
                   "description": f"还没有查询编号的数据（附件、网页里的、自己算出来的），一份一张卡片，最多 {MAX_TABLES} 份；"
                                  "查询工具查出来的结果不要抄进来，按 query_ids 展示"},
    },
    "additionalProperties": False,
}
TABLES_ONLY_SCHEMA = {**SCHEMA, "properties": {"tables": {
    **SCHEMA["properties"]["tables"],
    "description": f"要展示的数据（附件、网页里的、自己算出来的），一份一张卡片，最多 {MAX_TABLES} 份"}}}

DESCRIPTION = ("展示回答用到的结果：在回答下方画成卡片，表和图能切换、能换兼容的图形，底部写口径或来源。查询工具查出来的结果"
               "按 query_ids 展示；还没有查询编号的数据放进 tables。画什么图按数据的形状自动定：时间加数值画折线，分类加数值画"
               "柱状，能相加的一个数值、7 块以内画饼，几列数值按单位、量级自动分组。同一份数据只展示一次。")
TABLES_ONLY_DESCRIPTION = ("展示回答用到的数据：在回答下方画成卡片，表和图能切换、能换兼容的图形，底部写数据从哪来。画什么图按"
                           "数据的形状自动定：时间加数值画折线，分类加数值画柱状，能相加的一个数值、7 块以内画饼，几列数值按单位、"
                           "量级自动分组。")
GUIDANCE = ("要给用户看一份数据（趋势、对比、占比、明细）时用 show_result 展示，一次调用列出要展示的全部结果，不要在沙箱里"
            "用 Python 画图片给用户看；只有要放进文件（Word、PPT、Excel、PDF）的图才在沙箱里画。查询工具查出来的结果按 "
            "query_ids 展示，不要把它的数抄进 tables；tables 只放还没有查询编号的数据，先汇总成一行一个时间点或一个分类，"
            "每列标清 kind，数值写成数字、单位写进 unit，source 写清数据从哪来、怎么算的，算过的把代码放进 code。"
            "展示后正文写结论和怎么读，不要再贴同一张表。")
TABLES_ONLY_GUIDANCE = ("要给用户看一份数据（趋势、对比、占比、明细）时用 show_result 展示，一次调用列出要展示的全部数据，不要在沙箱里"
                        "用 Python 画图片给用户看；只有要放进文件（Word、PPT、Excel、PDF）的图才在沙箱里画。数据先汇总成一行一个"
                        "时间点或一个分类，每列标清 kind，数值写成数字、单位写进 unit，source 写清数据从哪来、怎么算的，算过的把"
                        "代码放进 code。展示后正文写结论和怎么读，不要再贴同一张表。")


@dataclass(frozen=True)
class _Table:
    title: str
    row_count: int
    chart: dict[str, Any] | None
    cards: list[dict[str, Any]]
    numbers: list[list[Any]]  # 每一列数值（比对是不是抄的查询结果）


def build_show_result(sources: ResultSources) -> BuiltinTool:
    """sources 由扩展装配时往里登记（ApiContext.results）；展示、交工具描述时现取。"""

    async def tailor(_ctx: ToolContext, spec: ToolSpec) -> ToolSpec:
        if not sources.items:  # 没有结果来源：没有查询编号可展示，只留 tables
            return replace(spec, description=TABLES_ONLY_DESCRIPTION, input_schema=TABLES_ONLY_SCHEMA,
                           guidance=TABLES_ONLY_GUIDANCE)
        extra = "".join(source.guidance for source in sources.items if source.guidance)
        return replace(spec, answer_modes=spec.answer_modes | sources.modes, guidance=GUIDANCE + extra)

    async def handler(ctx: ToolContext, arguments: dict[str, Any]) -> ToolOutput:
        ids = _ids(arguments.get("query_ids"))
        raw = arguments.get("tables") or []
        if not isinstance(raw, list) or len(raw) > MAX_TABLES:
            raise ToolFailure(f"tables 最多 {MAX_TABLES} 份")
        if not ids and not raw:
            raise ToolFailure("query_ids、tables 至少给一个")
        if raw and ctx.answer_mode not in INLINE_MODES:
            raise ToolFailure(INLINE_ONLY_IN_AGENT)
        tables = [_table(item, index) for index, item in enumerate(raw, start=1)]
        known = await _session_results(sources, ctx) if tables else []
        kept, notes = [], []
        for index, table in enumerate(tables, start=1):
            copied = next((result for result in reversed(known) if same_numbers(table.numbers, result)), None)
            if copied is None:
                kept.append(table)
            elif copied.id in ids:
                notes.append(f"tables 第 {index} 份就是 query_ids 里 {copied.id} 的结果，只展示一次")
            elif copied.shown:
                notes.append(f"tables 第 {index} 份就是查询 {copied.id} 的结果，前面已经展示过，不再出一张")
            else:
                ids.append(copied.id)
                notes.append(f"tables 第 {index} 份就是查询 {copied.id} 的结果，按那次查询展示（带口径）")
        shown: list[Any] = []
        cards: list[dict[str, Any]] = []
        for source, group in _grouped(sources, ids):
            outcome = await source.show(ctx, tuple(group))
            if outcome.payload.get("status") != "ok":
                return ToolOutput(_json(outcome.payload))
            shown.extend(outcome.payload.get("shown", []))
            cards.extend(outcome.cards)
        for table in kept:
            shown.append({"table": table.title, "rows": table.row_count,
                          "chart": CHART_NAMES[table.chart["type"]] if table.chart else "形状画不成图，只有表"})
            cards.extend(table.cards)
        presentation = normalize({"version": VERSION, "cards": cards}) if cards else None
        if cards and presentation is None:
            raise ToolFailure("这份数据展示不出来，检查 columns 和 rows")
        payload: dict[str, Any] = {"status": "ok", "shown": shown, **({"notes": notes} if notes else {})}
        if any(table.chart is None for table in kept):
            payload["drawable"] = DRAWABLE
        return ToolOutput(_json(payload), presentation)

    spec = ToolSpec(NAME, DESCRIPTION, SCHEMA, "read", guidance=GUIDANCE, concurrent=False, tailor=tailor)
    return BuiltinTool(spec, handler)


def _ids(raw: Any) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_IDS or not all(isinstance(item, str) and item.strip() for item in raw):
        raise ToolFailure(f"query_ids 要给查询编号，最多 {MAX_IDS} 个")
    return list(dict.fromkeys(item.strip() for item in raw))


async def _session_results(sources: ResultSources, ctx: ToolContext) -> list[Result]:
    found: list[Result] = []
    for source in sources.items:
        found.extend(await source.results(ctx))
    return found


def _grouped(sources: ResultSources, ids: list[str]) -> list[tuple[ResultSource, list[str]]]:
    """按来源分组、保持先后；没有来源认的编号直接报错（不是本会话查询工具给的）。"""
    groups: dict[int, tuple[ResultSource, list[str]]] = {}
    for result_id in ids:
        source = sources.owner(result_id)
        if source is None:
            raise ToolFailure(f"不认识的查询编号 {result_id}：query_id 要取自本会话查询工具成功返回的结果")
        groups.setdefault(id(source), (source, []))[1].append(result_id)
    return list(groups.values())


def _table(raw: Any, index: int) -> _Table:
    if not isinstance(raw, dict):
        raise ToolFailure(f"tables 第 {index} 份要是一个对象")
    try:
        columns = _columns(raw.get("columns"))
        rows = _rows(raw.get("rows"), columns)
        source = _text(raw.get("source"))
        if not source:
            raise ToolFailure("source 要写清数据从哪来、怎么算的")
    except ToolFailure as error:
        raise ToolFailure(f"tables 第 {index} 份：{error}") from error
    chart = chart_by_shape(_shape_columns(columns, rows), [ROLES[c["kind"]] for c in columns], rows,
                           additive=frozenset(c["name"] for c in columns if c["additive"]))
    title = _text(raw.get("title")) or _title(columns)
    subtitle, code = _text(raw.get("subtitle")), _text(raw.get("code"))
    table = {"type": "table", "title": title, **({"subtitle": subtitle} if subtitle else {}),
             "columns": [{"name": c["name"], "label": c["name"], "kind": CARD_KINDS.get(c["kind"], "text")}
                         for c in columns],
             "rows": rows, "row_count": len(rows), "more_rows": False, **({"chart": chart} if chart else {})}
    provenance = {"type": "provenance", "label": LABEL, "text": source, "items": _details(raw.get("details")),
                  **({"code": code, "code_language": _text(raw.get("code_language")) or "text"} if code else {})}
    numbers = [[row[i] for row in rows] for i, c in enumerate(columns) if c["kind"] == "number"]
    return _Table(title=title, row_count=len(rows), chart=chart, cards=[table, provenance], numbers=numbers)


def _columns(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not 0 < len(raw) <= MAX_COLUMNS:
        raise ToolFailure(f"columns 要有 1 到 {MAX_COLUMNS} 列")
    columns: list[dict[str, Any]] = []
    for item in raw:
        name = str(item.get("name", "")).strip()[:MAX_NAME] if isinstance(item, dict) else ""
        if not name or item.get("kind") not in KINDS:
            raise ToolFailure("每列都要有 name，kind 是 time、category、number、text 之一")
        if any(c["name"] == name for c in columns):
            raise ToolFailure(f"列名「{name}」重复了，每列的名字要不一样")
        unit = item.get("unit")
        columns.append({"name": name, "kind": item["kind"], "unit": unit.strip() if isinstance(unit, str) else "",
                        "additive": item.get("additive") is True})
    if not any(c["kind"] == "number" for c in columns):
        raise ToolFailure("至少要有一列 kind 是 number 的数值")
    return columns


def _rows(raw: Any, columns: list[dict[str, Any]]) -> list[list[Any]]:
    if not isinstance(raw, list) or not 0 < len(raw) <= MAX_ROWS:
        raise ToolFailure(f"rows 要有 1 到 {MAX_ROWS} 行；数据多就先汇总")
    width = len(columns)
    for index, row in enumerate(raw, start=1):
        if not isinstance(row, list) or len(row) != width:
            raise ToolFailure(f"第 {index} 行要有 {width} 个值，和 columns 一一对应")
        for column, value in zip(columns, row, strict=True):
            numeric = isinstance(value, int | float) and not isinstance(value, bool)
            if column["kind"] == "number" and value is not None and not numeric:
                raise ToolFailure(f"第 {index} 行「{column['name']}」是 {json.dumps(value, ensure_ascii=False)}，"
                                  "数值要写成数字，不带单位、千分位、百分号")
            if value is not None and not numeric and not isinstance(value, str):
                raise ToolFailure(f"第 {index} 行「{column['name']}」只能是文字、数字或 null")
    return [list(row) for row in raw]


def _shape_columns(columns: list[dict[str, Any]], rows: list[list[Any]]) -> list[dict[str, Any]]:
    """交给形状推断的列：type 按取值定（整列文字的分类才可能画饼、写成年月的文字当时间），数值列带上单位。"""
    shaped = []
    for index, column in enumerate(columns):
        values = [row[index] for row in rows if row[index] is not None]
        textual = all(isinstance(v, str) for v in values)
        column_type = {"time": "date", "number": "number"}.get(column["kind"], "text" if textual else "number")
        shaped.append({"name": column["name"], "type": column_type, **({"unit": column["unit"]} if column["unit"] else {})})
    return shaped


def _title(columns: list[dict[str, Any]]) -> str:
    """同查询结果的卡片标题：「销售额、毛利 · 按月、区域」。"""
    measures = "、".join(c["name"] for c in columns if c["kind"] == "number")
    groups = [c["name"] for c in columns if c["kind"] in ("time", "category")]
    return measures + (f" · 按{'、'.join(groups)}" if groups else "")


def _details(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    return [{"name": str(i["name"]).strip(), "value": str(i.get("value", "")).strip()} for i in raw[:MAX_DETAILS]
            if isinstance(i, dict) and str(i.get("name", "")).strip()]


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)
