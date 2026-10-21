import pytest

from knowwork_server.knowledge.models import Evidence
from knowwork_server.knowledge.rrf import rrf_merge, rrf_scores


def ev(source, span):
    return Evidence(source_id=source, doc_ref="d", span_ref=span, title="t", text=span)


def test_rrf_scores_follow_the_formula():
    scores = rrf_scores([["a", "b"], ["b", "c"]], k=60)
    assert scores["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert scores["a"] == pytest.approx(1 / 61)
    assert scores["c"] == pytest.approx(1 / 62)


def test_rrf_merge_interleaves_sources_and_dedupes():
    first = [ev("kf1", "x1"), ev("kf1", "x2"), ev("kf1", "x3")]
    second = [ev("kf2", "y1"), ev("kf2", "y2")]
    merged = rrf_merge([first, second], top_n=4)
    assert [e.span_ref for e in merged] == ["x1", "y1", "x2", "y2"]


def test_rrf_merge_boosts_items_present_in_several_rankings():
    a = [ev("s", "p"), ev("s", "q")]
    b = [ev("s", "q"), ev("s", "r")]
    merged = rrf_merge([a, b], top_n=10)
    assert merged[0].span_ref == "q"
    assert len(merged) == 3


def test_rrf_merge_ties_are_stable_by_first_appearance():
    merged = rrf_merge([[ev("a", "1")], [ev("b", "1")]], top_n=5)
    assert [e.source_id for e in merged] == ["a", "b"]


def test_rrf_rejects_bad_parameters():
    with pytest.raises(ValueError):
        rrf_scores([["a"]], k=0)
    with pytest.raises(ValueError):
        rrf_merge([], top_n=0)
    assert rrf_merge([], top_n=3) == []
