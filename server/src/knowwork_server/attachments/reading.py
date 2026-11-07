"""给模型读附件的两档（照 Claude 的做法：网页版把文档转成文字直接放进上下文，放不下的才按页、按段读）。

- 放得下的（单个不超过 inline_limit 字、这一轮还剩预算）：全文直接放进这一轮，模型不用调工具读文件，一步就能答；
- 放不下的：给字数、页数和目录（标题或页码在那份文字里的行号），模型先看目录，再按行号分段读、或 grep 着找，
  不从头一段段往下读。目录取 Markdown 标题、「第几章」「一、」这类中文章节（Word 用加粗当标题时 pandoc 认不出层级），
  标题太少（多半是 PDF）就按「[第 n 页]」列，每页带一句开头。
纯函数，不碰存储与沙箱。
"""

import re
from dataclasses import dataclass

HEADING = re.compile(r"^(#{1,6})\s+\S")
CHAPTER = re.compile(r"^(第[一二三四五六七八九十百千零〇\d]+[章节篇部分卷]|[一二三四五六七八九十]+、)\s*\S")
PAGE = re.compile(r"^\[第 (\d+) 页\]$")
MAX_OUTLINE = 120
MAX_ENTRY = 80       # 目录里每一条最多几个字
MAX_CHAPTER = 60     # 比这长的「一、……」多半是正文里的一句，不算标题
INFERRED = 7         # 中文章节编号认出来的标题，排在 Markdown 各级标题之后


@dataclass(frozen=True)
class OutlineEntry:
    line: int
    text: str


@dataclass(frozen=True)
class Reading:
    text: str | None                  # 放得下时的全文
    chars: int
    pages: int | None
    outline: tuple[OutlineEntry, ...]


def plan(text: str, *, inline_limit: int, budget: int) -> Reading:
    chars, pages = len(text), _pages(text)
    if chars <= inline_limit and chars <= budget:
        return Reading(text=text, chars=chars, pages=pages, outline=())
    return Reading(text=None, chars=chars, pages=pages, outline=outline_of(text))


def outline_of(text: str, *, limit: int = MAX_OUTLINE) -> tuple[OutlineEntry, ...]:
    lines = text.splitlines()
    headings = [found for number, line in enumerate(lines, 1) if (found := _heading(number, line))]
    if len(headings) < 3 and _pages(text):
        return _by_page(lines)[:limit]
    for level in sorted({level for level, _ in headings}, reverse=True):
        if len(headings) <= limit:
            break
        kept = [h for h in headings if h[0] < level]
        if not kept:
            break
        headings = kept
    return tuple(entry for _, entry in headings[:limit])


def _heading(number: int, line: str) -> tuple[int, OutlineEntry] | None:
    stripped = line.strip()
    if found := HEADING.match(stripped):
        # pandoc 转 Word 的标题常带加粗记号（### **5．**授予合同），目录里去掉
        return len(found.group(1)), OutlineEntry(number, _short(stripped.replace("**", "")))
    plain = stripped.strip("*").strip()
    if len(plain) <= MAX_CHAPTER and CHAPTER.match(plain):
        return INFERRED, OutlineEntry(number, _short(plain))
    return None


def _by_page(lines: list[str]) -> tuple[OutlineEntry, ...]:
    entries = []
    for index, line in enumerate(lines):
        if not (found := PAGE.match(line.strip())):
            continue
        first = next((other for other in lines[index + 1:] if other.strip() and not PAGE.match(other.strip())), "")
        entries.append(OutlineEntry(index + 1, _short(f"第 {found.group(1)} 页：{' '.join(first.split())}")))
    return tuple(entries)


def _pages(text: str) -> int | None:
    count = sum(1 for line in text.splitlines() if PAGE.match(line.strip()))
    return count or None


def _short(text: str) -> str:
    return text if len(text) <= MAX_ENTRY else text[:MAX_ENTRY - 1] + "…"
