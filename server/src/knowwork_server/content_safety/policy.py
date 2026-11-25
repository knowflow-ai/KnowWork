"""内容安全策略的规则：敏感词怎么拆、上限多少、什么时候算填好了。这里全是纯函数与不可变值。

规则：字面匹配、区分大小写，不做同音、拼音、繁简。词表分两种：管理员自己填的自定义词表，和扩展登记的现成词表
（sources.py，开源版没有）；每个空间勾用哪几种，勾上的合在一起查。
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from .sources import CUSTOM_SOURCE

MAX_WORDS = 30_000
MAX_WORDS_BYTES = 1024 * 1024  # 规范化以后（一行一个）按 UTF-8 算
MAX_WORD_CHARS = 256
MAX_MESSAGE_CHARS = 500
MAX_RECORDED_WORDS = 16  # 审计里记几个命中的词（审计详情的列表最多 16 项），多了另记总数

DEFAULT_MESSAGE = "当前对话内容违反公司规范，请修改后重新输入"

# 换行、中英文逗号与分号都算分隔：管理员常从表格或别的系统里整段粘过来
_SEPARATORS = re.compile(r"[\r\n,，;；]+")


class PolicyInvalid(ValueError):
    """保存的内容不合规则；message 直接给管理员看。"""


def split_words(raw: str) -> tuple[str, ...]:
    """拆成一个个词：去掉两头的空白（词中间的空格保留，例如英文项目名），去掉空的，重复的只留第一次出现的。"""
    seen: dict[str, None] = {}
    for part in _SEPARATORS.split(raw):
        word = part.strip()
        if word:
            seen.setdefault(word, None)
    return tuple(seen)


def holdback_of(words: tuple[str, ...]) -> int:
    """流式输出时每段要留着不放的字数：最长词长减一。跨两段切开的词，接上留着的尾巴就能整个查到。"""
    return max((len(word) for word in words), default=1) - 1


@dataclass(frozen=True)
class PolicyDraft:
    """管理员要保存的内容（已经规范化、校验过）。"""

    input_enabled: bool
    output_enabled: bool
    word_sources: tuple[str, ...]  # 勾了哪几种词表：custom（自定义）和扩展登记的来源
    words: tuple[str, ...]
    block_message: str

    @property
    def enabled(self) -> bool:
        return self.input_enabled or self.output_enabled


def draft_of(*, input_enabled: bool, output_enabled: bool, words: str, block_message: str,
             word_sources: Sequence[str] = (CUSTOM_SOURCE,),
             available: frozenset[str] = frozenset()) -> PolicyDraft:
    """规范化并校验。两个方向都不勾就是关闭，关闭时不要求填词和提示（填了的照样留着，下次打开不用重填）；
    上限不管开没开都校验，免得把超大的词表存进库里。available 是扩展登记了的来源，勾了不存在的来源拒收。"""
    sources = tuple(dict.fromkeys(word_sources))
    unknown = next((source for source in sources if source != CUSTOM_SOURCE and source not in available), None)
    if unknown is not None:
        raise PolicyInvalid(f"没有这种词表：{unknown}")
    parsed = split_words(words)
    message = block_message.strip()
    too_long = next((word for word in parsed if len(word) > MAX_WORD_CHARS), None)
    if too_long is not None:
        raise PolicyInvalid(f"有一个词超过 {MAX_WORD_CHARS} 个字：{too_long[:20]}…")
    if len(parsed) > MAX_WORDS:
        raise PolicyInvalid(f"敏感词最多 {MAX_WORDS} 个，现在有 {len(parsed)} 个")
    if len("\n".join(parsed).encode()) > MAX_WORDS_BYTES:
        raise PolicyInvalid("敏感词合计不能超过 1 MB")
    if len(message) > MAX_MESSAGE_CHARS:
        raise PolicyInvalid(f"阻断提示不能超过 {MAX_MESSAGE_CHARS} 个字")
    draft = PolicyDraft(input_enabled=input_enabled, output_enabled=output_enabled, word_sources=sources,
                        words=parsed, block_message=message)
    if draft.enabled and not sources:
        raise PolicyInvalid("请至少选一种词表")
    if draft.enabled and CUSTOM_SOURCE in sources and not parsed:
        raise PolicyInvalid("用自定义词表就要填敏感词")
    if draft.enabled and not message:
        raise PolicyInvalid("开了检查就要填阻断提示")
    return draft


@dataclass(frozen=True)
class Policy:
    """存着的一份策略。version 0 表示这个空间还没保存过（就是关闭）。"""

    workspace_id: str
    version: int
    input_enabled: bool
    output_enabled: bool
    word_sources: tuple[str, ...]
    words: tuple[str, ...]  # 自定义词表
    block_message: str
    updated_by: str | None
    updated_at: datetime | None

    @property
    def enabled(self) -> bool:
        """勾了检查方向（实际查不查还要看勾的词表里有没有词，见 service 编自动机）。"""
        return self.input_enabled or self.output_enabled


def unset_policy(workspace_id: str) -> Policy:
    return Policy(workspace_id=workspace_id, version=0, input_enabled=False, output_enabled=False,
                  word_sources=(CUSTOM_SOURCE,), words=(), block_message=DEFAULT_MESSAGE, updated_by=None,
                  updated_at=None)


DIRECTIONS = ("input", "output", "tool_args")


def direction_enabled(policy: Policy, direction: str) -> bool:
    """这个方向查不查：输入看「检查输入」，回答和工具参数都看「检查输出」。"""
    if not policy.enabled:
        return False
    return policy.input_enabled if direction == "input" else policy.output_enabled
