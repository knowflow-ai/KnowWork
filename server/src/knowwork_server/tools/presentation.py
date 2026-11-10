"""工具结果里只给界面看的那份（卡片）：处理函数在给模型的文本之外，可以另交一份卡片数据。

走 DSH 官方的扩展点：工具的输出声明里 `render` 给模型、`presentationMeta` 给界面，后者落在 `tool/result` 事件的
`meta` 上、不进模型上下文，回放时原样重现。用它而不是把卡片塞进工具结果，是因为结果表可能有几十行：模型已经在
自己的那份结果里看过，再看一遍只是占上下文。

协议 v1（`{"version": 1, "cards": [...]}`），按卡片种类：

- table：结果表。`columns` 每列 `{name, label?, kind?}`（kind 是 number / text / date，只管对齐），`rows` 是前几行
  （单元格只能是字符串、数字、布尔或空），`row_count` 是这次结果一共多少行，`more_rows` 表示结果本身还被截过；
  `title`、`subtitle`（可选）是卡片头上的标题与它下面一行灰字（例如「销售额 · 按区域」「2026-07-01 至 2026-09-30」）；
  `chart`（可选）说怎么把这张表画成图：`{type, x, y, series?}`，type 是 line / bar / pie，x 是横轴（饼图的分类）那一列，
  y 是要画的数值列（一列或几列），series 是按它分成几条线的那一列；`groups`（可选，有 series 时不认）把 y 分成几组，
  同一组的画在一张图里（几条线、几组柱子），组之间可以切换，没有时一列一组；都写列名，对不上表里的列就不带图，
  分组没正好分完 y 就不分组。画什么由工具定（例如问数按结果的形状推断），界面只照着画，用户可以在兼容的图形之间切换、也可以切回表；
  `drill`（可选）说哪几列的取值能点着往下钻：`{name, ref, columns}`，name 是扩展在前端插槽 chat.card.drill 里登记的
  组件名，ref 原样交给它（例如查询编号），columns 是能点的那几列（列名，对不上表里的列的去掉，一列都不剩就不带）；
  前端没登记那个组件时值不能点，下钻本身由扩展的接口判权；
- provenance：出处。`label` 是最前面那个词（如「口径」），`text` 一行说明，`items` 展开后的明细 `{name, value}`，
  `code` 是展开后的代码块（如 SQL，`code_language` 标语言），`open` 为真时默认展开；`actions` 是这张卡上的操作
  `{name, ref}`（最多 6 个）：name 是扩展在前端插槽 chat.card.actions 里登记的操作名，ref 原样交给它（例如查询编号）。
  前端没登记的操作不显示；操作本身由扩展的接口判权，这里只管形状；
- suggestions：几个可以接着问的问题（例如查不了时的换个问法、示例问题）。`title`、`text`（可选）是标题与一段说明，
  `questions` 是问题（最多 6 个），界面上点了就在这个对话里问；一个问题都没有的不要。

这里只做形状校验与截断，界面只渲染文本、不渲染 HTML。认不出的卡片丢掉、不让整份失败；一张都认不出时返回 None。
工具执行完与投影给前端时各过一次：前者限制回执里存多少，后者是交给浏览器前的最后一道。
"""

import json
import re
from typing import Any

VERSION = 1
MAX_CARDS = 10
MAX_COLUMNS = 40
MAX_ROWS = 100
MAX_CELL = 500
MAX_TEXT = 1000
MAX_ITEMS = 20
MAX_CODE = 20_000
MAX_BYTES = 256 * 1024
COLUMN_KINDS = ("number", "text", "date")
MAX_ACTIONS = 6
CHART_TYPES = ("line", "bar", "pie")
MAX_CHART_Y = 8
MAX_REF = 256
MAX_DRILL_REF = 4000  # 下钻编号可以带着前面钻过的几步（例如看板卡片），比操作的 ref 长；超了不截，整个不带
MAX_QUESTIONS = 6
MAX_QUESTION = 200
ACTION_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def normalize(value: Any) -> dict[str, Any] | None:
    """校验并截断一份卡片数据；认不出返回 None。返回的是新对象，不改入参。"""
    if not isinstance(value, dict) or value.get("version") != VERSION or not isinstance(value.get("cards"), list):
        return None
    cards = [card for card in (_card(item) for item in value["cards"][:MAX_CARDS]) if card is not None]
    if not cards:
        return None
    normalized: dict[str, Any] | None = {"version": VERSION, "cards": cards}
    while normalized is not None and len(json.dumps(normalized, ensure_ascii=False).encode()) > MAX_BYTES:
        normalized = _shrunk(normalized)
    return normalized


def _card(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    if item.get("type") == "table":
        return _table(item)
    if item.get("type") == "provenance":
        return _provenance(item)
    if item.get("type") == "suggestions":
        return _suggestions(item)
    return None


def _table(item: dict[str, Any]) -> dict[str, Any] | None:
    columns = [_column(c) for c in item.get("columns", [])[:MAX_COLUMNS]] if isinstance(item.get("columns"), list) else []
    if not columns or any(c is None for c in columns):
        return None
    width = len(columns)
    rows = item.get("rows") if isinstance(item.get("rows"), list) else []
    shown = [[_cell(v) for v in row[:width]] + [None] * (width - len(row[:width])) for row in rows[:MAX_ROWS]
             if isinstance(row, list)]
    row_count = item.get("row_count")
    table = {"type": "table", "columns": columns, "rows": shown,
             "row_count": row_count if isinstance(row_count, int) and row_count >= len(shown) else len(shown),
             "more_rows": item.get("more_rows") is True}
    title, subtitle = _text(item.get("title")), _text(item.get("subtitle"))
    names = {c["name"] for c in columns}
    chart, drill = _chart(item.get("chart"), names), _drill(item.get("drill"), names)
    return {**table, **({"title": title} if title else {}), **({"subtitle": subtitle} if subtitle else {}),
            **({"chart": chart} if chart else {}), **({"drill": drill} if drill else {})}


def _drill(value: Any, names: set[str]) -> dict[str, Any] | None:
    """能下钻的那几列：组件名合规、有编号（截断了就对不上，超长的不带）、列都在表里。"""
    if not isinstance(value, dict) or not isinstance(value.get("name"), str) or not ACTION_NAME.fullmatch(value["name"]):
        return None
    ref, columns = value.get("ref"), value.get("columns")
    if not isinstance(ref, str) or not ref.strip() or len(ref) > MAX_DRILL_REF or not isinstance(columns, list):
        return None
    kept = list(dict.fromkeys(c for c in columns if isinstance(c, str) and c in names))
    return {"name": value["name"], "ref": ref, "columns": kept} if kept else None


def _chart(value: Any, names: set[str]) -> dict[str, Any] | None:
    """怎么画：类型认得、列名都在表里、横轴和分组不和数值列重；有一样不对就不画（表照样给）。"""
    if not isinstance(value, dict) or value.get("type") not in CHART_TYPES:
        return None
    x, y, series = value.get("x"), value.get("y"), value.get("series")
    if not isinstance(x, str) or x not in names or not isinstance(y, list) or not 0 < len(y) <= MAX_CHART_Y:
        return None
    if not all(isinstance(v, str) and v in names and v != x for v in y) or len(set(y)) != len(y):
        return None
    if series is not None and (not isinstance(series, str) or series not in names or series == x or series in y):
        return None
    groups = None if series else _groups(value.get("groups"), y)
    return {"type": value["type"], "x": x, "y": list(y), **({"series": series} if series else {}),
            **({"groups": groups} if groups else {})}


def _groups(value: Any, y: list[str]) -> list[list[str]] | None:
    """几列数值的分组：正好把 y 分完、每列只出现一次才要；不对就不分组（一列一列画），图照样画。"""
    if not isinstance(value, list) or not all(isinstance(g, list) and g for g in value):
        return None
    flat = [name for group in value for name in group]
    if not all(isinstance(name, str) for name in flat) or len(flat) != len(y) or set(flat) != set(y):
        return None
    return [list(group) for group in value]


def _column(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"]:
        return None
    column: dict[str, Any] = {"name": item["name"][:MAX_CELL]}
    label = _text(item.get("label"))
    if label:
        column["label"] = label
    if item.get("kind") in COLUMN_KINDS:
        column["kind"] = item["kind"]
    return column


def _cell(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float):
        return value
    return str(value)[:MAX_CELL]


def _provenance(item: dict[str, Any]) -> dict[str, Any] | None:
    label, text = _text(item.get("label")), _text(item.get("text"))
    if not label and not text:
        return None
    card: dict[str, Any] = {"type": "provenance", "label": label, "text": text, "open": item.get("open") is True}
    items = item.get("items") if isinstance(item.get("items"), list) else []
    pairs = [{"name": _text(i.get("name")), "value": _text(i.get("value"))} for i in items[:MAX_ITEMS]
             if isinstance(i, dict) and _text(i.get("name"))]
    if pairs:
        card["items"] = pairs
    if isinstance(item.get("code"), str) and item["code"].strip():
        card["code"] = item["code"][:MAX_CODE]
        card["code_language"] = _text(item.get("code_language"))[:20] or "text"
    actions = _actions(item.get("actions"))
    if actions:
        card["actions"] = actions
    return card


def _suggestions(item: dict[str, Any]) -> dict[str, Any] | None:
    raw = item.get("questions") if isinstance(item.get("questions"), list) else []
    questions: list[str] = []
    for value in raw:
        question = value.strip()[:MAX_QUESTION] if isinstance(value, str) else ""
        if question and question not in questions:
            questions.append(question)
    if not questions:
        return None
    title, text = _text(item.get("title")), _text(item.get("text"))
    return {"type": "suggestions", **({"title": title} if title else {}), **({"text": text} if text else {}),
            "questions": questions[:MAX_QUESTIONS]}


def _actions(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    kept = [{"name": a["name"], "ref": a["ref"][:MAX_REF]} for a in value
            if isinstance(a, dict) and isinstance(a.get("name"), str) and ACTION_NAME.fullmatch(a["name"])
            and isinstance(a.get("ref"), str) and a["ref"].strip()]
    return kept[:MAX_ACTIONS]


def _text(value: Any) -> str:
    return value.strip()[:MAX_TEXT] if isinstance(value, str) else ""


def _shrunk(presentation: dict[str, Any]) -> dict[str, Any] | None:
    """超了总字节数时先砍表格的行（每次砍一半），砍到一行都没有还超就去掉最后一张卡；只剩一张也砍不动了返回 None。"""
    cards = presentation["cards"]
    index = next((i for i, card in enumerate(cards) if card["type"] == "table" and card["rows"]), None)
    if index is not None:
        table = cards[index]
        cards = [*cards[:index], {**table, "rows": table["rows"][:len(table["rows"]) // 2]}, *cards[index + 1:]]
        return {**presentation, "cards": cards}
    return {**presentation, "cards": cards[:-1]} if len(cards) > 1 else None
