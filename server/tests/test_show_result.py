"""展示结果（show_result）：回答下方的结果卡片只有这一个入口。
- tables：模型交的数据拼成卡片（表 + 怎么画 + 底部「来源」），画什么按形状推断（tools/charts.py），参数不对时说清哪里要改；
- query_ids：交给登记了这种编号的结果来源（例如扩展的问数），卡片由来源给；
- 同一份数据只出一张：tables 里的数值就是本会话某次查询的结果时按那次查询展示，展示过的不再出；
- 只有智能体收 tables；按会话裁剪描述：没有结果来源时只留 tables，有来源时加上来源的回答方式和使用规则。"""

import json
from dataclasses import dataclass, field

import pytest
from jsonschema import Draft202012Validator

from knowwork_server.tools.registry import ToolContext, ToolFailure
from knowwork_server.tools.results import Result, ResultSources, Shown, same_numbers
from knowwork_server.tools.show_result import INLINE_ONLY_IN_AGENT, SCHEMA, build_show_result

pytestmark = pytest.mark.anyio
CTX = ToolContext(workspace_id="ws", session_id="s1", user_id="u1")
MONTH = {"name": "月份", "kind": "time"}
REGION = {"name": "区域", "kind": "category"}
SALES = {"name": "销售额", "kind": "number", "unit": "万元", "additive": True}


@dataclass
class FakeQueries:
    """假的结果来源：编号 q_ 开头，本会话两次查询。"""

    found: list[Result] = field(default_factory=list)
    shown_calls: list[tuple[str, ...]] = field(default_factory=list)
    modes: frozenset[str] = frozenset({"numbers"})
    guidance: str = "查数：给数之前先展示它出自的查询。"

    def owns(self, result_id: str) -> bool:
        return result_id.startswith("q_")

    async def results(self, ctx):
        return self.found

    async def show(self, ctx, ids):
        self.shown_calls.append(ids)
        if any(i not in {r.id for r in self.found} for i in ids):
            return Shown({"status": "error", "code": "unknown_query", "candidates": [r.id for r in self.found]})
        cards = [{"type": "table", "title": f"查询 {i}", "columns": [{"name": "n"}], "rows": [[1]]} for i in ids]
        return Shown({"status": "ok", "shown": [{"query_id": i} for i in ids]}, cards)


MONTHLY = Result(id="q_monthly", shown=False, columns=[{"name": "month"}, {"name": "revenue"}],
                 rows=[["2026-06-01", "100"], ["2026-07-01", "2300"], ["2026-08-01", "2200.5"]])


def tool(*sources):
    registry = ResultSources()
    for source in sources:
        registry.add(source)
    return build_show_result(registry)


async def show(arguments, *sources, ctx=CTX):
    return await tool(*sources).handler(ctx, arguments)


async def cards_of(**table):
    output = await show({"tables": [{"source": "附件 2025年销售.xlsx，按区域求和", **table}]})
    card, provenance = output.presentation["cards"]
    return output.content, card, provenance


def test_schema_is_valid_json_schema():
    Draft202012Validator.check_schema(SCHEMA)


async def test_tailored_for_the_session_by_the_registered_sources():
    plain = await tool().spec.tailor(CTX, tool().spec)
    assert "query_ids" not in json.dumps(plain.input_schema, ensure_ascii=False) and "query_ids" not in plain.guidance, (
        "没有结果来源时参数和规则里都不提 query_ids")
    assert plain.answer_modes == frozenset({"agent"})
    queries = FakeQueries()
    with_source = tool(queries)
    tailored = await with_source.spec.tailor(CTX, with_source.spec)
    assert "query_ids" in tailored.input_schema["properties"]
    assert tailored.answer_modes == frozenset({"agent", "numbers"}), "来源自己的回答方式里也给 show_result"
    assert tailored.guidance.endswith(queries.guidance) and tailored.describe()["approval"] == "never"


async def test_monthly_values_are_a_line_with_a_title_like_query_cards_and_a_source_footer():
    content, card, provenance = await cards_of(
        columns=[MONTH, SALES], rows=[["2026-01", 120.5], ["2026-02", 98], ["2026-03", None]],
        subtitle="2026-01-01 至 2026-03-31", code="df.groupby('月份')['销售额'].sum()", code_language="python")
    assert card["chart"] == {"type": "line", "x": "月份", "y": ["销售额"]}
    assert card["title"] == "销售额 · 按月份" and card["subtitle"] == "2026-01-01 至 2026-03-31"
    assert [c["kind"] for c in card["columns"]] == ["date", "number"] and card["row_count"] == 3
    assert provenance["label"] == "来源" and provenance["code_language"] == "python"
    assert '"chart": "折线图"' in content


async def test_parts_of_a_whole_are_a_pie_and_other_values_are_bars():
    rows = [["华东", 120], ["华南", 80], ["华北", 60]]
    assert (await cards_of(columns=[REGION, SALES], rows=rows))[1]["chart"]["type"] == "pie"
    average = {"name": "客单价", "kind": "number", "unit": "元"}
    assert (await cards_of(columns=[REGION, average], rows=rows))[1]["chart"] == {"type": "bar", "x": "区域", "y": ["客单价"]}


async def test_measures_are_grouped_by_unit_and_other_shapes_follow_the_query_rules():
    target = {"name": "目标", "kind": "number", "unit": "万元"}
    orders = {"name": "订单数", "kind": "number", "unit": "单"}
    _, card, _ = await cards_of(columns=[MONTH, SALES, target, orders],
                                rows=[["2026-01", 120, 130, 3000], ["2026-02", 98, 110, 2600]])
    assert card["chart"]["groups"] == [["销售额", "目标"], ["订单数"]]
    _, card, _ = await cards_of(columns=[MONTH, REGION, SALES],
                                rows=[["2026-01", "华东", 1], ["2026-01", "华南", 2], ["2026-02", "华东", 3]])
    assert card["chart"] == {"type": "line", "x": "月份", "y": ["销售额"], "series": "区域"}
    _, card, _ = await cards_of(columns=[{"name": "月份", "kind": "category"}, SALES], rows=[["2026年1月", 1], ["2026年2月", 2]])
    assert card["chart"]["type"] == "line", "写成年月的分类当时间"
    content, card, _ = await cards_of(columns=[MONTH, SALES], rows=[["2026-01", 1], ["2026-01", 2]])
    assert "chart" not in card and "每个时间一行" in content, "画不成图时告诉模型能画的形状"


@pytest.mark.parametrize(("table", "message"), [
    ({"columns": [MONTH, SALES], "rows": [["2026-01", "1,234"]]}, "数值要写成数字"),
    ({"columns": [MONTH, SALES], "rows": [["2026-01"]]}, "要有 2 个值"),
    ({"columns": [MONTH, REGION], "rows": [["2026-01", "华东"]]}, "至少要有一列"),
    ({"columns": [SALES, SALES], "rows": [[1, 2]]}, "重复"),
    ({"columns": [MONTH, SALES], "rows": [["2026-01", 1]] * 101}, "先汇总"),
    ({"columns": [{"name": "月份", "kind": "date"}, SALES], "rows": [["2026-01", 1]]}, "kind"),
    ({"columns": [MONTH, SALES], "rows": [["2026-01", True]]}, "数值要写成数字"),
    ({"columns": [MONTH, SALES], "rows": [["2026-01", 1]], "source": "  "}, "source"),
])
async def test_wrong_tables_tell_the_model_what_to_fix(table, message):
    with pytest.raises(ToolFailure, match=f"tables 第 1 份：.*{message}"):
        await show({"tables": [{"source": "附件", **table}]})


async def test_nothing_to_show_or_unknown_ids_are_refused():
    with pytest.raises(ToolFailure, match="至少给一个"):
        await show({})
    with pytest.raises(ToolFailure, match="不认识的查询编号"):  # 没有来源认这个编号
        await show({"query_ids": ["q_1"]})


async def test_query_results_are_shown_by_their_source_and_errors_pass_through():
    queries = FakeQueries(found=[MONTHLY])
    output = await show({"query_ids": ["q_monthly", "q_monthly"]}, queries)
    assert queries.shown_calls == [("q_monthly",)], "同一个编号只展示一次"
    assert [c["title"] for c in output.presentation["cards"]] == ["查询 q_monthly"]
    failed = await show({"query_ids": ["q_nope"]}, queries)
    assert '"unknown_query"' in failed.content and failed.presentation is None, "来源的报错和候选原样交给模型"


async def test_query_results_copied_into_tables_are_shown_as_the_query():
    queries = FakeQueries(found=[MONTHLY])
    copied = {"columns": [MONTH, SALES], "rows": [["2026-06", 100], ["2026-07", 2300], ["2026-08", 2200.5]],
              "source": "样例销售库，按月求和"}
    output = await show({"tables": [copied]}, queries)
    assert queries.shown_calls == [("q_monthly",)], "按那次查询展示，带口径"
    assert [c["title"] for c in output.presentation["cards"]] == ["查询 q_monthly"], "不另出一张"
    assert "按那次查询展示" in output.content
    both = await show({"query_ids": ["q_monthly"], "tables": [copied]}, FakeQueries(found=[MONTHLY]))
    assert len(both.presentation["cards"]) == 1 and "只展示一次" in both.content
    shown_before = FakeQueries(found=[Result(id="q_monthly", shown=True, columns=MONTHLY.columns, rows=MONTHLY.rows)])
    again = await show({"tables": [copied]}, shown_before)
    assert again.presentation is None and shown_before.shown_calls == [] and "前面已经展示过" in again.content


async def test_tables_are_only_taken_in_the_agent_mode():
    queries = FakeQueries(found=[MONTHLY])
    table = {"columns": [MONTH, SALES], "rows": [["2026-01", 1]], "source": "附件"}
    with pytest.raises(ToolFailure, match=INLINE_ONLY_IN_AGENT):
        await show({"tables": [table]}, queries, ctx=ToolContext("ws", "s1", "u1", answer_mode="numbers"))
    shown = await show({"query_ids": ["q_monthly"]}, queries, ctx=ToolContext("ws", "s1", "u1", answer_mode="numbers"))
    assert shown.presentation is not None, "扩展的回答方式里按编号照样展示"


def test_same_numbers_compares_values_column_by_column_within_rounding():
    assert same_numbers([[100, 2300, 2200.5]], MONTHLY)
    assert same_numbers([[2200.49, 100, 2300]], MONTHLY), "顺序不论，四舍五入到两位也算"
    assert not same_numbers([[100, 2300, 2400]], MONTHLY)
    assert not same_numbers([[100, 2300]], MONTHLY), "行数不一样"
    assert not same_numbers([[None, None, None]], MONTHLY), "一个数都没有不算"
