"""智能体的 show_chart：交上来的数据拼成和问数一样的结果卡片（表 + 「怎么画」+ 底部出处），画什么按形状推断
（tools/charts.py）；参数不对时把哪里不对告诉模型，让它改了再调。"""

import pytest
from jsonschema import Draft202012Validator

from knowwork_server.tools.registry import ToolContext, ToolFailure
from knowwork_server.tools.show_chart import SCHEMA, SHOW_CHART, show_chart

pytestmark = pytest.mark.anyio
CTX = ToolContext(workspace_id="ws", session_id="s1", user_id="u1")
MONTH = {"name": "月份", "kind": "time"}
REGION = {"name": "区域", "kind": "category"}
SALES = {"name": "销售额", "kind": "number", "unit": "万元", "additive": True}


async def _cards(**arguments):
    output = await show_chart(CTX, {"source": "附件 2025年销售.xlsx，按区域求和", **arguments})
    table, provenance = output.presentation["cards"]
    return output.content, table, provenance


def test_schema_is_valid_json_schema_and_spec_goes_to_the_agent():
    Draft202012Validator.check_schema(SCHEMA)
    described = SHOW_CHART.spec.describe()
    assert described["name"] == "show_chart" and described["approval"] == "never" and described["guidance"]


async def test_monthly_values_are_a_line_with_a_title_like_analytics_and_a_source_footer():
    content, table, provenance = await _cards(
        columns=[MONTH, SALES], rows=[["2026-01", 120.5], ["2026-02", 98], ["2026-03", None]],
        subtitle="2026-01-01 至 2026-03-31", code="df.groupby('月份')['销售额'].sum()", code_language="python")
    assert table["chart"] == {"type": "line", "x": "月份", "y": ["销售额"]}
    assert table["title"] == "销售额 · 按月份" and table["subtitle"] == "2026-01-01 至 2026-03-31"
    assert [c["kind"] for c in table["columns"]] == ["date", "number"] and table["row_count"] == 3
    assert provenance["label"] == "来源" and provenance["text"].startswith("附件")
    assert provenance["code_language"] == "python"
    assert "折线图" in content and "不要再贴这张表" in content


async def test_parts_of_a_whole_are_a_pie_and_other_values_are_bars():
    rows = [["华东", 120], ["华南", 80], ["华北", 60]]
    assert (await _cards(columns=[REGION, SALES], rows=rows))[1]["chart"]["type"] == "pie"
    average = {"name": "客单价", "kind": "number", "unit": "元"}  # 平均值加起来没意义，同问数只给柱状
    assert (await _cards(columns=[REGION, average], rows=rows))[1]["chart"] == {"type": "bar", "x": "区域", "y": ["客单价"]}


async def test_measures_are_grouped_by_unit_like_analytics():
    target = {"name": "目标", "kind": "number", "unit": "万元"}
    orders = {"name": "订单数", "kind": "number", "unit": "单"}
    _, table, _ = await _cards(columns=[MONTH, SALES, target, orders],
                               rows=[["2026-01", 120, 130, 3000], ["2026-02", 98, 110, 2600]])
    assert table["chart"]["groups"] == [["销售额", "目标"], ["订单数"]]


async def test_time_and_one_category_split_into_lines_and_two_categories_are_grouped_bars():
    _, table, _ = await _cards(columns=[MONTH, REGION, SALES],
                               rows=[["2026-01", "华东", 1], ["2026-01", "华南", 2], ["2026-02", "华东", 3]])
    assert table["chart"] == {"type": "line", "x": "月份", "y": ["销售额"], "series": "区域"}
    level = {"name": "客户等级", "kind": "category"}
    _, table, _ = await _cards(columns=[REGION, level, SALES],
                               rows=[["华东", "A", 1], ["华东", "B", 2], ["华南", "A", 3], ["华北", "A", 4]])
    assert table["chart"] == {"type": "bar", "x": "区域", "y": ["销售额"], "series": "客户等级"}


async def test_month_written_as_category_text_is_still_a_line_and_one_month_twice_is_not_drawn():
    _, table, _ = await _cards(columns=[{"name": "月份", "kind": "category"}, SALES],
                               rows=[["2026年1月", 1], ["2026年2月", 2]])
    assert table["chart"]["type"] == "line"
    content, table, _ = await _cards(columns=[MONTH, SALES], rows=[["2026-01", 1], ["2026-01", 2]])
    assert "chart" not in table and "画不成图" in content and "每个时间一行" in content


@pytest.mark.parametrize(("arguments", "message"), [
    ({"columns": [MONTH, SALES], "rows": [["2026-01", "1,234"]]}, "数值要写成数字"),
    ({"columns": [MONTH, SALES], "rows": [["2026-01"]]}, "要有 2 个值"),
    ({"columns": [MONTH, REGION], "rows": [["2026-01", "华东"]]}, "至少要有一列"),
    ({"columns": [SALES, SALES], "rows": [[1, 2]]}, "重复"),
    ({"columns": [MONTH, SALES], "rows": [["2026-01", 1]] * 101}, "先汇总"),
    ({"columns": [{"name": "月份", "kind": "date"}, SALES], "rows": [["2026-01", 1]]}, "kind"),
    ({"columns": [MONTH, SALES], "rows": [["2026-01", True]]}, "数值要写成数字"),
])
async def test_wrong_arguments_tell_the_model_what_to_fix(arguments, message):
    with pytest.raises(ToolFailure, match=message):
        await show_chart(CTX, {"source": "附件", **arguments})


async def test_source_is_required():
    with pytest.raises(ToolFailure, match="source"):
        await show_chart(CTX, {"columns": [MONTH, SALES], "rows": [["2026-01", 1]], "source": "  "})
