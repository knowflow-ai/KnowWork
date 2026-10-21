"""倒数排名融合（Reciprocal Rank Fusion，Cormack 等 2009）。

查过 ranx、LangChain EnsembleRetriever 等现成实现：公式只有一行，为它引入整套检索框架不划算，
这里写成纯函数。score(d) = Σ 1 / (k + rank_i(d))，rank 从 1 开始，k 取论文推荐的 60。
各知识源的原始分数口径不同，不参与融合，只用名次。
"""

from collections.abc import Hashable, Sequence

from .models import Evidence

DEFAULT_K = 60


def rrf_scores[K: Hashable](rankings: Sequence[Sequence[K]], *, k: int = DEFAULT_K) -> dict[K, float]:
    """每个键的融合分。同一排名里重复出现的键只按最靠前的名次计一次。"""
    if k <= 0:
        raise ValueError("k 必须为正数")
    scores: dict[K, float] = {}
    for ranking in rankings:
        seen: set[K] = set()
        for rank, key in enumerate(ranking, start=1):
            if key in seen:
                continue
            seen.add(key)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
    return scores


def rrf_merge(rankings: Sequence[Sequence[Evidence]], *, top_n: int, k: int = DEFAULT_K) -> list[Evidence]:
    """合并多个知识源的证据列表，按 (知识源, 片段) 去重，分数相同时按首次出现的先后。"""
    if top_n <= 0:
        raise ValueError("top_n 必须为正数")
    first_seen: dict[tuple[str, str], tuple[int, Evidence]] = {}
    for ranking in rankings:
        for item in ranking:
            first_seen.setdefault(item.key, (len(first_seen), item))
    scores = rrf_scores([[item.key for item in ranking] for ranking in rankings], k=k)
    ordered = sorted(first_seen.items(), key=lambda pair: (-scores[pair[0]], pair[1][0]))
    return [item for _key, (_order, item) in ordered[:top_n]]
