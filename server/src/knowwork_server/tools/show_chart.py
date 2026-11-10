"""智能体在回答下方展示图表（show_chart）：和问数的结果卡片一样的卡片，前端同一个组件画（ECharts）。

为什么不让模型画：沙箱里 matplotlib 出的是静态图片，样式和界面不一致，不能切换图形、切回表、悬停看数；让模型直接写
ECharts 配置也不行，配置里提示框的格式字符串按 HTML 渲染，附件、知识库里的注入就能变成页面上的脚本，样式也各画各的。
所以模型只交汇总好的数据和每列是什么（时间、分类、数值），画什么图按形状推断（tools/charts.py，问数也用这一份），
同样的数据总是同样的图。卡片照问数的格式：标题「数值 · 按分组」、一行灰字（时间范围）、底部一行出处（这里写「来源」，
点开看明细和算数的代码）。只有要放进文件（Word、PPT、Excel）的图才在沙箱里画。
"""

import json
from typing import Any

from .charts import chart_by_shape
from .presentation import MAX_COLUMNS, MAX_ROWS, VERSION, normalize
from .registry import BuiltinTool, ToolContext, ToolFailure, ToolOutput, ToolSpec

NAME = "show_chart"
LABEL = "来源"
KINDS = ("time", "category", "number", "text")
ROLES = {"time": "time", "category": "category", "number": "measure", "text": None}
CARD_KINDS = {"time": "date", "number": "number"}  # 界面上的列类型只管对齐与横轴是不是时间，其余都是 text
CHART_NAMES = {"line": "折线图", "bar": "柱状图", "pie": "饼图"}
MAX_NAME = 100
MAX_DETAILS = 20
DRAWABLE = ("画得成图的形状（至少两行）：一列时间加数值，每个时间一行（折线）；一列时间、一列分类加数值，每种组合一行"
            "（按分类分线，分类最多 8 个）；一列分类加数值（柱状；标了能相加的一个数值、7 块以内、分类不重复、都不为负时画饼）；"
            "两列分类加数值，每种组合一行（分组柱状）。")

CELL = {"oneOf": [{"type": "string"}, {"type": "number"}, {"type": "null"}]}
SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "卡片标题，写成「数值 · 按分组」，例如「销售额 · 按区域」「销售额、毛利 · 按月」；"
                                                   "不写就按列名拼"},
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
        "code": {"type": "string", "description": "算出这些数的代码（可选，例如汇总用的 Python 或 SQL），点开出处能看、能复制"},
        "code_language": {"type": "string", "description": "code 的语言，例如 python、sql"},
    },
    "required": ["columns", "rows", "source"],
    "additionalProperties": False,
}

DESCRIPTION = ("在回答下方展示一张图表卡片：图和表能切换、能换兼容的图形，底部写数据从哪来。"
               "画什么图按数据的形状自动定：时间加数值画折线，分类加数值画柱状，能相加的一个数值、7 块以内画饼，"
               f"几列数值按单位、量级自动分组。数据先汇总好，最多 {MAX_ROWS} 行，数值写成数字。")
GUIDANCE = ("要给用户看趋势、对比、占比时，用 show_chart 在回答下方展示图表，不要在沙箱里用 Python 画图片给用户看；"
            "只有要放进文件（Word、PPT、Excel、PDF）的图才在沙箱里画。数据先汇总成一行一个时间点或一个分类"
            "（明细先汇总，用 Python 算的交算好的结果），每列标清 kind，数值写成数字、单位写进 unit；source 写清数据从哪来、"
            "怎么算的，算过的把代码放进 code。图显示在回答下方，正文写结论和怎么读，不要再贴同一张表。")


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
    """同问数的卡片标题：「销售额、毛利 · 按月、区域」。"""
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


async def show_chart(_ctx: ToolContext, arguments: dict[str, Any]) -> ToolOutput:
    columns = _columns(arguments.get("columns"))
    rows = _rows(arguments.get("rows"), columns)
    source = _text(arguments.get("source"))
    if not source:
        raise ToolFailure("source 要写清数据从哪来、怎么算的")
    chart = chart_by_shape(_shape_columns(columns, rows), [ROLES[c["kind"]] for c in columns], rows,
                           additive=frozenset(c["name"] for c in columns if c["additive"]))
    title = _text(arguments.get("title")) or _title(columns)
    subtitle = _text(arguments.get("subtitle"))
    code = _text(arguments.get("code"))
    table = {"type": "table", "title": title, **({"subtitle": subtitle} if subtitle else {}),
             "columns": [{"name": c["name"], "label": c["name"], "kind": CARD_KINDS.get(c["kind"], "text")}
                         for c in columns],
             "rows": rows, "row_count": len(rows), "more_rows": False, **({"chart": chart} if chart else {})}
    provenance = {"type": "provenance", "label": LABEL, "text": source, "items": _details(arguments.get("details")),
                  **({"code": code, "code_language": _text(arguments.get("code_language")) or "text"} if code else {})}
    presentation = normalize({"version": VERSION, "cards": [table, provenance]})
    if presentation is None:
        raise ToolFailure("这份数据展示不出来，检查 columns 和 rows")
    if chart is None:
        return ToolOutput(f"已在回答下方展示「{title}」的表（{len(rows)} 行），这份数据的形状画不成图。{DRAWABLE}"
                          "要画图就按这个形状整理后再调一次。", presentation)
    return ToolOutput(f"已在回答下方展示「{title}」（{len(rows)} 行，画成{CHART_NAMES[chart['type']]}，用户能换图形、切回表）。"
                      "正文写结论和怎么读，不要再贴这张表，也不要再用别的办法画同一张图。", presentation)


SHOW_CHART = BuiltinTool(ToolSpec(NAME, DESCRIPTION, SCHEMA, "read", guidance=GUIDANCE, concurrent=True), show_chart)
