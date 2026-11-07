"""给模型读附件的两档：放得下的给全文，放不下的给字数、页数和目录（行号）。"""

from knowwork_server.attachments.reading import outline_of, plan


def test_small_text_goes_in_whole_within_the_budget():
    small = plan("# 付款条款\n合同签订后 30 天内付清", inline_limit=100, budget=100)
    assert (small.text, small.chars, small.outline) == ("# 付款条款\n合同签订后 30 天内付清", 20, ())
    over_budget = plan("x" * 60, inline_limit=100, budget=50)
    assert over_budget.text is None and over_budget.chars == 60, "一轮里前面的附件把预算用掉了，这份给目录"


def test_long_markdown_gets_its_headings_with_line_numbers():
    text = "\n".join(["# 采购文件", "说明", "## 第一章 邀请函", "正文" * 50, "## 第二章 须知", "### **1．**说明", "正文"])
    reading = plan(text, inline_limit=10, budget=1000)
    assert reading.text is None and reading.pages is None
    assert [(e.line, e.text) for e in reading.outline] == [
        (1, "# 采购文件"), (3, "## 第一章 邀请函"), (5, "## 第二章 须知"), (6, "### 1．说明")]


def test_chapters_written_in_bold_count_as_headings():
    """Word 里用加粗当标题的（pandoc 只认标题样式），按「第几章」「一、」认出来。"""
    text = "**第一章 邀请函**\n正文\n一、采购范围\n正文\n**第二章 供应商须知**\n附表一、报价"
    assert [(e.line, e.text) for e in outline_of(text)] == [
        (1, "第一章 邀请函"), (3, "一、采购范围"), (5, "第二章 供应商须知")]


def test_pdf_without_headings_is_listed_by_page():
    text = "[第 1 页]\n   山药    商品介绍   \n正文\n\n[第 2 页]\n\n如何区分  铁棍山药"
    reading = plan(text, inline_limit=5, budget=100)
    assert reading.pages == 2
    assert [(e.line, e.text) for e in reading.outline] == [(1, "第 1 页：山药 商品介绍"), (5, "第 2 页：如何区分 铁棍山药")]


def test_long_outlines_keep_the_upper_levels():
    """目录太长时先丢最细的一级，一级都放不下就只留前面的。"""
    lines = []
    for chapter in range(10):
        lines.append(f"# 第 {chapter} 章")
        lines.extend(f"## 小节 {chapter}-{n}" for n in range(30))
    outline = outline_of("\n".join(lines), limit=50)
    assert [e.text for e in outline] == [f"# 第 {c} 章" for c in range(10)]
    assert len(outline_of("\n".join(f"# 章 {n}" for n in range(80)), limit=50)) == 50
