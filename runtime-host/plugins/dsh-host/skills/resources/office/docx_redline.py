"""在原 docx 上改字，保留原格式；默认以修订痕迹写入，可附批注。

    python3 docx_redline.py 原文件.docx edits.json 输出.docx [--no-track] [--author 名字]

edits.json 是一个列表，每一项：
    {"find": "原文片段", "replace": "新文字"}         替换（replace 为 "" 即删除）
    {"find": "原文片段", "comment": "批注内容"}       只加批注，不改字
    {"find": "...", "replace": "...", "comment": "..."}  替换并在新文字上加批注
    "occurrence": 2                                    原文片段出现多次时改第几处（从 1 起）

规则：find 必须是某一个段落里连续的原文（可以跨加粗、字号不同的几段），
出现多次又没给 occurrence 就拒绝——宁可停下，也不改错地方。
新文字沿用被替换处第一个字的格式。任何一项失败，整份文件都不写。
"""

import argparse
import copy
import datetime
import json
import sys

from docx import Document
from docx.oxml.ns import qn
from docx.text.run import Run

W_R, W_T, W_RPR = qn("w:r"), qn("w:t"), qn("w:rPr")


class EditError(Exception):
    pass


def paragraphs(doc):
    yield from doc.element.body.iter(qn("w:p"))
    seen = set()
    for section in doc.sections:
        for part in (section.header, section.footer):
            element = part._element
            if id(element) in seen:
                continue
            seen.add(id(element))
            yield from element.iter(qn("w:p"))


def text_runs(paragraph):
    # 段落里直接的 run 与超链接里的 run。已经处在修订里的不碰。
    for child in paragraph:
        if child.tag == W_R:
            yield child
        elif child.tag == qn("w:hyperlink"):
            yield from (run for run in child if run.tag == W_R)


def run_text(run):
    return "".join(node.text or "" for node in run.iter(W_T))


def paragraph_text(paragraph):
    return "".join(run_text(run) for run in text_runs(paragraph))


def make_run(template, text, tag=W_T):
    run = copy.deepcopy(template)
    for child in list(run):
        if child.tag != W_RPR:
            run.remove(child)
    node = run.makeelement(tag, {})
    node.text = text
    node.set(qn("xml:space"), "preserve")
    run.append(node)
    return run


def split(run, offset):
    """把 run 在 offset 处切成两个，各自保留原格式。返回右半边。"""

    text = run_text(run)
    left, right = make_run(run, text[:offset]), make_run(run, text[offset:])
    run.addprevious(left)
    run.addprevious(right)
    run.getparent().remove(run)
    return right


def isolate(paragraph, start, end):
    """让 [start, end) 恰好由若干完整的 run 组成，返回这些 run。"""

    position = 0
    for run in list(text_runs(paragraph)):
        length = len(run_text(run))
        if position < start < position + length:
            run = split(run, start - position)
            position = start
            length = len(run_text(run))
        if position < end < position + length:
            split(run, end - position)
        position += length
    position, picked = 0, []
    for run in text_runs(paragraph):
        length = len(run_text(run))
        if length and start <= position and position + length <= end:
            picked.append(run)
        position += length
    return picked


def locate(doc, find):
    hits = []
    for paragraph in paragraphs(doc):
        text = paragraph_text(paragraph)
        start = text.find(find)
        while start >= 0:
            hits.append((paragraph, start))
            start = text.find(find, start + 1)
    return hits


class Revision:
    def __init__(self, doc, author):
        ids = [
            int(value)
            for value in doc.element.body.xpath("//@w:id")
            if str(value).isdigit()
        ]
        self._next = max(ids, default=0)
        self.author = author
        self.date = datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

    def element(self, parent, tag):
        self._next += 1
        return parent.makeelement(
            qn(tag),
            {
                qn("w:id"): str(self._next),
                qn("w:author"): self.author,
                qn("w:date"): self.date,
            },
        )


def apply_edit(doc, edit, revision, track):
    find = edit.get("find") or ""
    if not find:
        raise EditError("每一项都要有 find")
    replace = edit.get("replace")
    comment = edit.get("comment")
    if replace is None and not comment:
        raise EditError(f"「{find}」既没有 replace 也没有 comment")
    hits = locate(doc, find)
    occurrence = edit.get("occurrence")
    if not hits:
        raise EditError(f"找不到「{find}」（它必须是同一段落里连续的原文）")
    if occurrence is None and len(hits) > 1:
        raise EditError(f"「{find}」出现 {len(hits)} 次，请用 occurrence 指明改第几处")
    index = (occurrence or 1) - 1
    if not 0 <= index < len(hits):
        raise EditError(f"「{find}」只出现 {len(hits)} 次，没有第 {occurrence} 处")
    paragraph, start = hits[index]
    runs = isolate(paragraph, start, start + len(find))
    anchor = runs

    if replace is not None:
        new_run = make_run(runs[0], replace) if replace else None
        if track:
            deleted = revision.element(paragraph, "w:del")
            runs[0].addprevious(deleted)
            for run in runs:
                for node in list(run.iter(W_T)):
                    node.tag = qn("w:delText")
                deleted.append(run)
            if new_run is not None:
                inserted = revision.element(paragraph, "w:ins")
                inserted.append(new_run)
                deleted.addnext(inserted)
        else:
            if new_run is not None:
                runs[0].addprevious(new_run)
            for run in runs:
                run.getparent().remove(run)
        # 删掉的字上挂批注没有意义，批注跟着新文字；纯删除则挂在删除处。
        anchor = [new_run] if new_run is not None else (runs if track else [])

    if comment:
        if not anchor:
            raise EditError(f"「{find}」已被直接删除，没有地方挂批注")
        doc.add_comment(
            [Run(run, None) for run in anchor],
            text=comment,
            author=revision.author,
            initials=revision.author[:2],
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source")
    parser.add_argument("edits")
    parser.add_argument("output")
    parser.add_argument("--no-track", action="store_true", help="直接改，不留修订痕迹")
    parser.add_argument("--author", default="KnowWork Agent")
    args = parser.parse_args(argv)

    with open(args.edits, encoding="utf-8") as handle:
        edits = json.load(handle)
    if not isinstance(edits, list):
        raise SystemExit("edits.json 必须是一个列表")
    doc = Document(args.source)
    revision = Revision(doc, args.author)
    try:
        for number, edit in enumerate(edits, 1):
            try:
                apply_edit(doc, edit, revision, track=not args.no_track)
            except EditError as error:
                raise EditError(f"第 {number} 项：{error}") from None
    except EditError as error:
        print(f"没有写出文件。{error}", file=sys.stderr)
        return 1
    doc.save(args.output)
    print(f"已写出 {args.output}：{len(edits)} 处修改" + ("" if args.no_track else "（修订模式）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
