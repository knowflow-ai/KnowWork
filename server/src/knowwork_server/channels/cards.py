"""工具交给界面的卡片（tools/presentation.py）在 IM 里的样子，答完附在回答后面。

网页上卡片画在回答下面，模型的回答只写结论和关键的几个数（例如问数的结果表、口径、SQL 都在卡片里）；IM 里没有卡片，
不补上就只剩一句结论。IM 画不了图和能展开的出处，三个平台的 markdown 也不都认表格（钉钉、飞书卡片里的 markdown
都不画表），所以：
- 结果表写成列表：一行一条「- 分组：数」，分组是文字、日期列连起来，数是数值列（几个数时带列名）；只列前几行；
- 出处只留一行说明（「口径：……」），明细与代码（SQL）不发，认出的人点链接去网页看；
- 接着问的问题列出来（IM 里点不了，照着发一句就行）。
认不出的卡片跳过。
"""

from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

MAX_ROWS = 10
MAX_TABLES = 3  # 一轮展示了好几次结果时，IM 里只附前几张，免得一条消息被表占满
EMPTY = "—"


def render(cards: Sequence[dict[str, Any]]) -> str:
    parts: list[str] = []
    tables = 0
    for card in cards:
        kind = card.get("type")
        if kind == "table" and tables < MAX_TABLES:
            tables += 1
            parts.append(_table(card))
        elif kind == "provenance":
            parts.append(_provenance(card))
        elif kind == "suggestions":
            parts.append(_suggestions(card))
    return "\n\n".join(part for part in parts if part)


def has_details(cards: Sequence[dict[str, Any]]) -> bool:
    """有没有 IM 里只给了一部分的卡片（表的图、出处的明细与代码）：有才值得附网页链接。"""
    return any(card.get("type") in ("table", "provenance") for card in cards)


def _table(card: dict[str, Any]) -> str:
    columns = [c for c in card.get("columns", []) if isinstance(c, dict)]
    numeric = [i for i, c in enumerate(columns) if c.get("kind") == "number"]
    keys = [i for i in range(len(columns)) if i not in numeric]
    rows = [row for row in card.get("rows", []) if isinstance(row, list)]
    shown = rows[:MAX_ROWS]
    lines = [_head(card)]
    if not keys and len(shown) == 1:  # 不分组的合计：一个数一行
        lines += [f"- {_label(columns[i])}：{_value(shown[0], i, True)}" for i in numeric]
    else:
        lines += [_row(row, columns, keys, numeric) for row in shown]
    total = card.get("row_count") if isinstance(card.get("row_count"), int) else len(rows)
    if card.get("more_rows") is True:
        lines.append(f"……结果不止 {total} 行，这里只列前 {len(shown)} 行")
    elif total > len(shown):
        lines.append(f"……共 {total} 行，这里只列前 {len(shown)} 行")
    return "\n".join(lines)


def _head(card: dict[str, Any]) -> str:
    title = str(card.get("title") or "查询结果")
    subtitle = card.get("subtitle")
    return f"**{title}**（{subtitle}）" if subtitle else f"**{title}**"


def _row(row: list, columns: list[dict], keys: list[int], numeric: list[int]) -> str:
    key = " · ".join(_value(row, i, False) for i in keys)
    if not numeric:
        return f"- {key}"
    if len(numeric) == 1:
        values = _value(row, numeric[0], True)
    else:
        values = "；".join(f"{_label(columns[i])} {_value(row, i, True)}" for i in numeric)
    return f"- {key}：{values}" if key else f"- {values}"


def _label(column: dict) -> str:
    return str(column.get("label") or column.get("name") or "")


def _value(row: list, index: int, number: bool) -> str:
    value = row[index] if index < len(row) else None
    if value is None or value == "":
        return EMPTY
    if isinstance(value, bool):
        return "是" if value else "否"
    if number and isinstance(value, int | float | str):
        return _number(value)
    return str(value)


def _number(value: float | str) -> str:
    """数加千分位，小数位照原样（浮点先按 6 位舍掉二进制误差）；不是数的字符串原样给。"""
    try:
        exact = Decimal(str(round(value, 6))) if isinstance(value, float) else Decimal(str(value))
    except InvalidOperation:
        return str(value)
    if not exact.is_finite():
        return str(value)
    text = format(exact, ",")
    return text.rstrip("0").rstrip(".") if "." in text and isinstance(value, float) else text


def _provenance(card: dict[str, Any]) -> str:
    label, text = str(card.get("label") or ""), str(card.get("text") or "")
    if label and text:
        return f"{label}：{text}"
    return label or text


def _suggestions(card: dict[str, Any]) -> str:
    questions = [q for q in card.get("questions", []) if isinstance(q, str) and q]
    if not questions:
        return ""
    head = f"{card.get('title') or '可以这样问'}：{card.get('text') or ''}".rstrip()
    return "\n".join([head, *(f"- {q}" for q in questions)])
