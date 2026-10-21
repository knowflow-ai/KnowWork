import pytest

from knowwork_server.knowledge.citations import CitationCatalog
from knowwork_server.knowledge.models import Evidence


def ev(span, source="kf"):
    return Evidence(source_id=source, doc_ref="d1", span_ref=span, title="合同", text=f"正文{span}")


def test_register_assigns_sequential_numbers_and_reuses_for_same_span():
    catalog, numbers = CitationCatalog().register([ev("a"), ev("b")])
    assert numbers == (1, 2)
    catalog2, again = catalog.register([ev("b"), ev("c"), ev("a", source="other")])
    assert again == (2, 3, 4)
    assert len(catalog.entries) == 2, "原目录不被修改"
    assert catalog2.lookup(3).evidence.span_ref == "c"
    assert catalog2.lookup(99) is None


def test_overflow_is_marked_truncated_not_silently_dropped():
    catalog, numbers = CitationCatalog(limit=2).register([ev("a"), ev("b"), ev("c"), ev("d")])
    assert numbers == (1, 2, None, None)
    assert catalog.truncated and catalog.dropped == 2
    catalog, numbers = catalog.register([ev("a")])
    assert numbers == (1,), "已有的证据仍可复用编号"
    with pytest.raises(ValueError):
        CitationCatalog(limit=0)


def test_default_limit_is_500():
    catalog, numbers = CitationCatalog().register([ev(str(i)) for i in range(501)])
    assert numbers[-1] is None and numbers[-2] == 500
    assert catalog.truncated


def test_check_removes_unknown_numbers_and_warns():
    catalog, _ = CitationCatalog().register([ev("a"), ev("b")])
    result = catalog.check("付款期限为 30 天[1]，违约金见[7]。另见[2][2]，链接[1](http://x)不算。")
    assert result.text == "付款期限为 30 天[1]，违约金见。另见[2][2]，链接[1](http://x)不算。"
    assert result.cited == (1, 2)
    assert result.removed == (7,)
    assert any("[7]" in w for w in result.warnings)


def test_check_reports_truncation():
    catalog, _ = CitationCatalog(limit=1).register([ev("a"), ev("b")])
    result = catalog.check("无引用")
    assert result.cited == () and result.removed == ()
    assert any("截断" in w for w in result.warnings)
