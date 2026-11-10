"""工具交给界面的卡片在 IM 里的样子：IM 画不了表、图和能展开的出处，三个平台的 markdown 又不都认表格，
所以结果表写成「- 分组：数」的列表，只列前几行；出处只留一行说明（明细、SQL 去网页看）；接着问的问题列出来。
答完附在回答后面：认出的人给网页链接，群里（渠道账号的会话，别人打不开）不给。"""

import pytest

from knowwork_server.channels.cards import render

from .conftest import FakeConnection, run
from .test_people import member_channel, message, person

pytestmark = pytest.mark.anyio

TABLE = {"type": "table", "title": "销售额 · 按区域", "subtitle": "2026-07-01 至 2026-09-30",
         "columns": [{"name": "region", "label": "区域", "kind": "text"},
                     {"name": "amount", "label": "销售额", "kind": "number"}],
         "rows": [["华东", 1234567.5], ["华北", "987654"], ["西南", None]], "row_count": 3, "more_rows": False}
CALIBER = {"type": "provenance", "label": "口径", "text": "销售额 · 2026-07-01 至 2026-09-30 · 按区域",
           "items": [{"name": "指标", "value": "SUM(amount)"}], "code": "SELECT ...", "code_language": "sql"}


def test_a_grouped_result_becomes_a_list_with_its_caliber():
    assert render([TABLE, CALIBER]) == ("**销售额 · 按区域**（2026-07-01 至 2026-09-30）\n"
                                        "- 华东：1,234,567.5\n- 华北：987,654\n- 西南：—\n\n"
                                        "口径：销售额 · 2026-07-01 至 2026-09-30 · 按区域")


def test_several_numbers_per_row_and_totals_without_groups():
    table = {"type": "table", "title": "销售额、订单数 · 按月",
             "columns": [{"name": "month", "label": "月", "kind": "date"}, {"name": "region", "label": "区域"},
                         {"name": "amount", "label": "销售额", "kind": "number"},
                         {"name": "orders", "label": "订单数", "kind": "number"}],
             "rows": [["2026-07", "华东", 100, 3]], "row_count": 1, "more_rows": False}
    assert render([table]) == "**销售额、订单数 · 按月**\n- 2026-07 · 华东：销售额 100；订单数 3"
    total = {"type": "table", "title": "销售额、订单数",
             "columns": [{"name": "amount", "label": "销售额", "kind": "number"},
                         {"name": "orders", "label": "订单数", "kind": "number"}],
             "rows": [[100, 3]], "row_count": 1, "more_rows": False}
    assert render([total]) == "**销售额、订单数**\n- 销售额：100\n- 订单数：3"


def test_long_results_only_list_the_first_rows():
    rows = [[f"门店{i}", i] for i in range(30)]
    table = {**TABLE, "subtitle": None, "rows": rows, "row_count": 30}
    text = render([table])
    assert text.count("\n- ") == 10 and text.endswith("……共 30 行，这里只列前 10 行")
    assert render([{**table, "more_rows": True}]).endswith("……结果不止 30 行，这里只列前 10 行")


def test_suggestions_are_listed_and_unknown_cards_are_skipped():
    suggestions = {"type": "suggestions", "title": "可以这样问", "text": "库里没有利润的数据。",
                   "questions": ["上个月销售额是多少", "各区域订单数"]}
    assert render([{"type": "chart"}, suggestions]) == ("可以这样问：库里没有利润的数据。\n"
                                                        "- 上个月销售额是多少\n- 各区域订单数")
    assert render([]) == ""


async def test_cards_follow_the_answer_and_people_get_the_web_link(env):
    channel = await member_channel(env)
    await person(env, "u-zhang", "zhangsan")
    shown = ("item.completed", {"kind": "tool", "data": {"text": "{}", "presentation": {
        "version": 1, "cards": [TABLE, CALIBER]}}})
    env.conversations.scripts = [run(1, ("item.started", {"kind": "tool", "data": {"name": "show_result"}}), shown,
                                     ("item.completed", {"kind": "message",
                                                         "data": {"role": "assistant", "text": "华东最高。"}}),
                                     ("run.completed", {}))] * 2
    connection = FakeConnection()
    await env.channels.service.handle(message(channel, "各区域销售额"), connection)
    conversation = env.conversations.created[0][0]
    final = connection.streams[0].final
    assert final.startswith("华东最高。\n\n**销售额 · 按区域**")
    assert final.endswith(f"\n\n（完整的结果、图表和出处在网页里看：https://kw.example.com/chat/{conversation.id}）")

    # 群里用渠道账号：会话别人打不开，不给链接
    await env.channels.service.handle(message(channel, "各区域销售额", group="chat-1"), connection)
    final = connection.streams[1].final
    assert final.startswith("华东最高。\n\n**销售额 · 按区域**") and "网页" not in final
