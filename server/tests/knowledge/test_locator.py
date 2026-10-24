"""知识源协议里的定位：带类型、各类型只带自己的字段、框是占页面的比例。"""

import pytest
from pydantic import ValidationError

from knowwork_server.knowledge.models import MAX_QUOTE, Box, Locator, clip_quote


@pytest.mark.parametrize("fields", [
    {"type": "pdf", "page": 3, "boxes": [{"page": 3, "x0": 0.1, "y0": 0.2, "x1": 0.9, "y1": 0.25}]},
    {"type": "docx", "block": 12, "quote": "付款期限 30 天"},
    {"type": "docx", "block": 12, "start": 30, "end": 33, "quote": "付款期限 30 天"},
    {"type": "slide", "page": 4},
    {"type": "sheet", "sheet": "明细", "row_start": 2, "row_end": 40},
    {"type": "text", "start": 0, "end": 120},
    {"type": "pdf", "section_path": ["第三章", "付款"], "source_hash": "sha256:abc"},
])
def test_each_type_takes_its_own_fields(fields):
    locator = Locator.model_validate(fields)
    assert Locator.model_validate(locator.model_dump(mode="json")) == locator, "落库再读回来不变"


@pytest.mark.parametrize(("fields", "message"), [
    ({"type": "docx", "page": 2}, "docx 定位不能带 page"),
    ({"type": "text", "start": 1, "sheet": "a"}, "text 定位不能带 sheet"),
    ({"type": "pdf", "block": 1, "row_start": 2}, "pdf 定位不能带 block, row_start"),
    ({"type": "slide"}, "slide 定位要有幻灯片序号"),
    ({"type": "sheet", "row_start": 2}, "sheet 定位要有工作表名"),
    ({"type": "sheet", "sheet": "a", "row_start": 5, "row_end": 4}, "row_end 不能小于 row_start"),
    ({"type": "text", "start": 9, "end": 3}, "end 不能小于 start"),
    ({"type": "video", "page": 1}, "type"),
])
def test_mismatched_fields_are_rejected(fields, message):
    with pytest.raises(ValidationError, match=message):
        Locator.model_validate(fields)


def test_boxes_are_page_ratios():
    assert Box(page=1, x0=0, y0=0, x1=1, y1=1).x1 == 1
    for bad in ({"x0": 0, "y0": 0, "x1": 200, "y1": 1}, {"x0": -0.1, "y0": 0, "x1": 1, "y1": 1},
                {"x0": 0.5, "y0": 0, "x1": 0.4, "y1": 1}):
        with pytest.raises(ValidationError):
            Box(page=1, **bad)


def test_quote_is_whitespace_normalized_and_clipped():
    assert clip_quote("  付款\n\t期限  30 天 ") == "付款 期限 30 天"
    assert len(clip_quote("字" * 1000)) == MAX_QUOTE
    with pytest.raises(ValidationError):
        Locator(type="pdf", quote="字" * (MAX_QUOTE + 1))
