"""内容安全策略的规则：怎么拆词、上限、开了检查才要求填词和提示、流式要留几个字。"""

import pytest

from knowwork_server.content_safety.policy import (
    MAX_WORD_CHARS,
    MAX_WORDS,
    PolicyInvalid,
    direction_enabled,
    draft_of,
    holdback_of,
    split_words,
    unset_policy,
)


def test_split_accepts_newlines_and_both_kinds_of_commas_and_semicolons_and_dedupes():
    assert split_words("星河计划\n某某银行，Project X;星河计划；\r\n  客户甲 ,, ") == (
        "星河计划", "某某银行", "Project X", "客户甲")


def test_disabled_policy_keeps_words_without_requiring_them():
    draft = draft_of(input_enabled=False, output_enabled=False, words="", block_message="")
    assert not draft.enabled and draft.words == ()


@pytest.mark.parametrize(("words", "message", "problem"), [
    ("", "提示", "敏感词"),
    ("星河计划", "  ", "阻断提示"),
])
def test_enabled_policy_needs_words_and_a_message(words, message, problem):
    with pytest.raises(PolicyInvalid, match=problem):
        draft_of(input_enabled=True, output_enabled=False, words=words, block_message=message)


def test_limits_are_checked_even_when_disabled():
    with pytest.raises(PolicyInvalid, match="超过"):
        draft_of(input_enabled=False, output_enabled=False, words="长" * (MAX_WORD_CHARS + 1), block_message="")
    with pytest.raises(PolicyInvalid, match="最多"):
        draft_of(input_enabled=False, output_enabled=False,
                 words="\n".join(f"w{index}" for index in range(MAX_WORDS + 1)), block_message="")
    with pytest.raises(PolicyInvalid, match="阻断提示"):
        draft_of(input_enabled=True, output_enabled=True, words="词", block_message="长" * 501)


def test_holdback_is_longest_word_minus_one():
    assert holdback_of(("星河计划", "某行", "Project X")) == 8
    assert holdback_of(()) == 0


def test_directions_follow_the_two_checkboxes():
    policy = unset_policy("ws")
    assert not direction_enabled(policy, "input")
    on = policy.__class__(**{**policy.__dict__, "version": 1, "input_enabled": False, "output_enabled": True,
                             "words": ("词",)})
    assert not direction_enabled(on, "input")
    assert direction_enabled(on, "output") and direction_enabled(on, "tool_args")


def test_word_sources_rules():
    base = {"input_enabled": True, "output_enabled": False, "block_message": "提示"}
    builtin_only = draft_of(**base, words="", word_sources=["builtin"], available=frozenset({"builtin"}))
    assert builtin_only.word_sources == ("builtin",), "只用现成词表时不要求填自定义词"
    with pytest.raises(PolicyInvalid, match="没有这种词表"):
        draft_of(**base, words="词", word_sources=["builtin"])
    with pytest.raises(PolicyInvalid, match="至少选一种词表"):
        draft_of(**base, words="词", word_sources=[])
    with pytest.raises(PolicyInvalid, match="自定义词表就要填敏感词"):
        draft_of(**base, words="", word_sources=["custom", "builtin"], available=frozenset({"builtin"}))
