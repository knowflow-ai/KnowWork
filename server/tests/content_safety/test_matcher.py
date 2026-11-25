"""敏感词的 AC 自动机：多层重叠的词都查得到、随机词表与逐词查找结果一致、限条数但照实报总数。"""

import random
import time

from knowwork_server.content_safety.matcher import Matcher


def test_overlapping_words_at_every_layer_are_found():
    # 「中国人」的失败指针要退到「国人」再退到「人」：只退一层的写法会漏掉其中几个
    matcher = Matcher(["中国", "国人", "中国人", "人", "我是中国人民"])
    hits = matcher.find("我是中国人", limit=10)
    assert set(hits.words) == {"中国", "国人", "中国人", "人"}
    assert hits.total == 4


def test_matches_a_naive_search_on_random_word_lists():
    rng = random.Random(20261122)
    alphabet = "abcab星河计划"
    for _ in range(300):
        words = list(dict.fromkeys("".join(rng.choice(alphabet) for _ in range(rng.randint(1, 5)))
                                   for _ in range(rng.randint(1, 12))))
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 60)))
        expected = {word for word in words if word in text}
        hits = Matcher(words).find(text, limit=len(words))
        assert set(hits.words) == expected, (words, text)
        assert hits.total == len(expected)


def test_limit_caps_the_list_but_total_counts_every_distinct_word():
    words = [f"词{index}" for index in range(30)]
    hits = Matcher(words).find("".join(words) + "词0词1", limit=5)
    assert hits.words == ("词0", "词1", "词2", "词3", "词4")
    assert hits.total == 30


def test_literal_case_sensitive_and_beyond_the_basic_plane():
    matcher = Matcher(["Project X", "😀机密"])
    assert matcher.find("project x 和 Project X", limit=5).words == ("Project X",)
    assert matcher.find("看这个😀机密文件", limit=5).words == ("😀机密",)
    assert matcher.find("没有命中", limit=5).total == 0


def test_thirty_thousand_words_compile_and_scan_quickly():
    rng = random.Random(7)
    chars = [chr(code) for code in range(0x4E00, 0x4E00 + 3000)]
    words = list(dict.fromkeys("".join(rng.choice(chars) for _ in range(rng.randint(2, 6))) for _ in range(30_000)))
    started = time.perf_counter()
    matcher = Matcher(words)
    text = "".join(rng.choice(chars) for _ in range(60_000)) + words[123]
    # 随机的 6 万字里本来就会碰上一些两字词，不限条数再看放在末尾的那个
    hits = matcher.find(text, limit=len(words))
    elapsed = time.perf_counter() - started
    assert words[123] in hits.words
    assert set(hits.words) == {word for word in words if word in text}
    assert elapsed < 10, f"3 万个词编译加扫 6 万字用了 {elapsed:.1f} 秒"
