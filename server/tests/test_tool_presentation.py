"""工具交给界面的卡片（tools/presentation.py）：只认协议里的形状，截断到上限，认不出的丢掉、不改入参。"""

import copy
import json

from knowwork_server.tools import presentation
from knowwork_server.tools.presentation import normalize

TABLE = {"type": "table", "title": " 结果 ", "columns": [{"name": "region", "label": "区域", "kind": "text"},
                                                         {"name": "n", "kind": "number"}],
         "rows": [["华东", 5], ["华南", 3]], "row_count": 2}
PROVENANCE = {"type": "provenance", "label": "口径", "text": "订单数 · 2026 年第三季度",
              "items": [{"name": "过滤", "value": "剔除测试订单"}], "code": "SELECT 1", "code_language": "sql", "open": True}


def test_known_cards_pass_through_and_unknown_ones_are_dropped():
    value = {"version": 1, "cards": [TABLE, {"type": "chart", "series": []}, PROVENANCE, "不是卡片"]}
    before = copy.deepcopy(value)
    out = normalize(value)
    assert [c["type"] for c in out["cards"]] == ["table", "provenance"]
    assert out["cards"][0]["title"] == "结果" and out["cards"][0]["more_rows"] is False
    with_subtitle = normalize({"version": 1, "cards": [{**TABLE, "subtitle": " 2026-07-01 至 2026-09-30 "}]})
    assert with_subtitle["cards"][0]["subtitle"] == "2026-07-01 至 2026-09-30", "卡片头标题下的一行灰字"
    assert out["cards"][1] == {**PROVENANCE, "open": True}
    assert value == before, "不改入参"


def test_shapes_that_are_not_the_protocol_give_none():
    assert normalize(None) is None
    assert normalize({"version": 2, "cards": [TABLE]}) is None
    assert normalize({"version": 1, "cards": [{"type": "chart"}]}) is None
    assert normalize({"version": 1, "cards": [{**TABLE, "columns": [{"label": "没名字"}]}]}) is None
    assert normalize({"version": 1, "cards": [{"type": "provenance", "label": " ", "text": ""}]}) is None


def test_cells_rows_and_text_are_bounded():
    wide = {**TABLE, "rows": [["华东", {"nested": True}, "多出来的列"], ["只有一列"]] + [["x", 1]] * 500,
            "row_count": 1}
    table = normalize({"version": 1, "cards": [wide]})["cards"][0]
    assert table["rows"][0] == ["华东", "{'nested': True}"], "单元格只留标量，按列数截齐"
    assert table["rows"][1] == ["只有一列", None], "短的行补空"
    assert len(table["rows"]) == presentation.MAX_ROWS
    assert table["row_count"] == presentation.MAX_ROWS, "总行数不能比给出的行少"
    long = normalize({"version": 1, "cards": [{**PROVENANCE, "text": "长" * 5000, "code": "S" * 50_000}]})["cards"][0]
    assert len(long["text"]) == presentation.MAX_TEXT and len(long["code"]) == presentation.MAX_CODE


def test_total_size_is_capped_by_cutting_rows_first():
    columns = [{"name": f"c{i}"} for i in range(4)]
    big = {"type": "table", "columns": columns, "rows": [["长" * 500] * 4 for _ in range(100)], "row_count": 100}
    assert len(json.dumps(big, ensure_ascii=False).encode()) > presentation.MAX_BYTES, "数据确实超了上限"
    out = normalize({"version": 1, "cards": [big, PROVENANCE]})
    assert len(json.dumps(out, ensure_ascii=False).encode()) <= presentation.MAX_BYTES
    assert [c["type"] for c in out["cards"]] == ["table", "provenance"], "先砍行，不先丢卡片"
    assert 0 < len(out["cards"][0]["rows"]) < 100 and out["cards"][0]["row_count"] == 100, "总行数照旧，界面写「共 100 行」"


def test_provenance_actions_keep_only_well_formed_ones():
    card = {"type": "provenance", "label": "口径", "text": "按口径查", "actions": [
        {"name": "save_trusted_query", "ref": "anql_1"}, {"name": "Bad Name", "ref": "x"}, {"name": "ok", "ref": ""},
        "not a dict", *({"name": f"a{i}", "ref": "r"} for i in range(2, 9))]}
    [kept] = normalize({"version": 1, "cards": [card]})["cards"]
    assert kept["actions"] == [{"name": "save_trusted_query", "ref": "anql_1"},
                               *({"name": f"a{i}", "ref": "r"} for i in range(2, 7))], \
        "名字不合规、没有 ref 的丢掉，剩下的最多 6 个（问数的出处卡片上有改条件、下载、加到看板、存为可信查询四个）"


def test_table_charts_must_name_columns_of_the_table():
    """表格卡片的「怎么画」：类型认得、列名都在表里才带上；不对就不画，表照样给。"""
    columns = [{"name": "month"}, {"name": "region"}, {"name": "revenue", "kind": "number"},
               {"name": "orders", "kind": "number"}]
    table = {"type": "table", "columns": columns, "rows": [["2026-01", "华东", 1, 2]]}

    def chart_of(chart):
        return normalize({"version": 1, "cards": [{**table, "chart": chart}]})["cards"][0].get("chart")

    assert chart_of({"type": "line", "x": "month", "y": ["revenue", "orders"]}) == {
        "type": "line", "x": "month", "y": ["revenue", "orders"]}
    assert chart_of({"type": "line", "x": "month", "y": ["revenue"], "series": "region"})["series"] == "region"
    for bad in ({"type": "radar", "x": "month", "y": ["revenue"]}, {"type": "bar", "x": "nope", "y": ["revenue"]},
                {"type": "bar", "x": "month", "y": []}, {"type": "bar", "x": "month", "y": ["month"]},
                {"type": "bar", "x": "month", "y": ["revenue", "revenue"]},
                {"type": "line", "x": "month", "y": ["revenue"], "series": "revenue"}, "line"):
        assert chart_of(bad) is None, bad


def test_table_drill_keeps_columns_of_the_table():
    """表格卡片的下钻：组件名合规、有编号、列在表里才带上；对不上的列去掉，一列不剩、编号超长就不带（截断的编号对不上）。"""
    table = {"type": "table", "columns": [{"name": "region"}, {"name": "n", "kind": "number"}], "rows": [["华东", 1]]}

    def drill_of(drill):
        return normalize({"version": 1, "cards": [{**table, "drill": drill}]})["cards"][0].get("drill")

    assert drill_of({"name": "drill_query", "ref": "anql_1", "columns": ["region", "gone", "region"]}) == {
        "name": "drill_query", "ref": "anql_1", "columns": ["region"]}
    assert drill_of({"name": "drill_card", "ref": "b/c/" + "x" * 3000, "columns": ["region"]})["ref"].startswith("b/c/")
    for bad in ({"name": "Bad Name", "ref": "r", "columns": ["region"]}, {"name": "d", "ref": "", "columns": ["region"]},
                {"name": "d", "ref": "r", "columns": ["gone"]}, {"name": "d", "ref": "x" * 5000, "columns": ["region"]},
                {"name": "d", "ref": "r"}, "drill"):
        assert drill_of(bad) is None, bad


def test_suggestions_card_lists_questions_to_ask_next():
    """suggestions：一段说明加几个可以接着问的问题（例如查不了时给的换个问法），界面上点了就在这个对话里问。
    问题去空、去重、截断，最多 6 个；标题、说明都可以不给，一个问题都没有的不要。"""
    shown = normalize({"version": 1, "cards": [{
        "type": "suggestions", "title": "这个问题查不了", "text": "模型里没有「提成」这个指标",
        "questions": ["  各区域的成交金额  ", "各区域的成交金额", "", 3, *[f"问题{i}" for i in range(10)]]}]})
    [card] = shown["cards"]
    assert card == {"type": "suggestions", "title": "这个问题查不了", "text": "模型里没有「提成」这个指标",
                    "questions": ["各区域的成交金额", "问题0", "问题1", "问题2", "问题3", "问题4"]}
    assert normalize({"version": 1, "cards": [{"type": "suggestions", "questions": ["x" * 500]}]})["cards"][0][
        "questions"] == ["x" * 200]
    assert normalize({"version": 1, "cards": [{"type": "suggestions", "title": "只有标题", "questions": []}]}) is None
