"""引用目录的纯逻辑。全部不可变：每次注册返回新目录。

- 工具返回的每条证据注册进本轮目录，分配编号 [n]；同一片段（知识源 + 片段引用）复用同一编号；
- 总量上限默认 500 条，超出的证据不分配编号，目录标记 truncated 并记下丢了几条，绝不静默截断；
- 校验回答：只认目录里存在的 [n]，不存在的去掉并给出警告；Markdown 链接 [n](...) 不当作引用；
  回答里的图 [IMG:n:j] 只认第 n 条证据真有的第 j 张图，别的去掉（不放行编出来的编号）。
落库见 citation_store.py。
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, replace

from .models import Evidence

DEFAULT_LIMIT = 500
_MARK = re.compile(r"\[(\d{1,6})\](?!\()")
IMAGE_MARK = re.compile(r"\[IMG:(\d{1,6}):(\d{1,3})\]")


@dataclass(frozen=True)
class CitationEntry:
    number: int
    evidence: Evidence


@dataclass(frozen=True)
class CitationCheck:
    text: str
    cited: tuple[int, ...]
    removed: tuple[int, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class CitationCatalog:
    entries: tuple[CitationEntry, ...] = ()
    limit: int = DEFAULT_LIMIT
    truncated: bool = False
    dropped: int = 0

    def __post_init__(self) -> None:
        if self.limit <= 0:
            raise ValueError("引用上限必须为正数")

    def lookup(self, number: int) -> CitationEntry | None:
        if 1 <= number <= len(self.entries):
            return self.entries[number - 1]
        return None

    def register(self, evidences: Iterable[Evidence]) -> tuple["CitationCatalog", tuple[int | None, ...]]:
        index = {entry.evidence.key: entry.number for entry in self.entries}
        entries = list(self.entries)
        numbers: list[int | None] = []
        dropped = 0
        for item in evidences:
            number = index.get(item.key)
            if number is None and len(entries) < self.limit:
                number = len(entries) + 1
                entries.append(CitationEntry(number, item))
                index[item.key] = number
            elif number is None:
                dropped += 1
            numbers.append(number)
        catalog = replace(self, entries=tuple(entries), truncated=self.truncated or dropped > 0,
                          dropped=self.dropped + dropped)
        return catalog, tuple(numbers)

    def check(self, answer: str) -> CitationCheck:
        cited: list[int] = []
        removed: list[int] = []

        def substitute(match: re.Match) -> str:
            number = int(match.group(1))
            if self.lookup(number) is None:
                removed.append(number)
                return ""
            if number not in cited:
                cited.append(number)
            return match.group(0)

        bad_images: list[str] = []

        def picture(match: re.Match) -> str:
            entry = self.lookup(int(match.group(1)))
            if entry is None or int(match.group(2)) >= len(entry.evidence.images):
                bad_images.append(match.group(0))
                return ""
            return match.group(0)

        text = _MARK.sub(substitute, IMAGE_MARK.sub(picture, answer))
        warnings = [f"回答引用了不存在的编号 [{n}]，已去掉" for n in dict.fromkeys(removed)]
        warnings += [f"回答里的图 {mark} 不在本轮结果里，已去掉" for mark in dict.fromkeys(bad_images)]
        if self.truncated:
            warnings.append(f"本轮证据超过 {self.limit} 条上限，已截断 {self.dropped} 条，未分配编号")
        return CitationCheck(text=text, cited=tuple(cited), removed=tuple(dict.fromkeys(removed)),
                             warnings=tuple(warnings))
