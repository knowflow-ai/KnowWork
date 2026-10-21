"""接受 docx 里的全部修订，得到干净稿。

    python3 accept_changes.py 输入.docx 输出.docx

删除的内容去掉，插入的内容留下并去掉修订外壳，格式修订（rPrChange、pPrChange）
按新格式保留。段落标记被删除的段落并入下一段——Word 接受修订时就是这样，
所以一个全部内容都被删掉的段落会整段消失，而不是留下一个空行或空的编号项。
"""

import argparse
import os
import shutil
import sys
import tempfile

from lxml import etree

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ooxml import pack, q, unpack  # noqa: E402

CHANGE_MARKS = ("rPrChange", "pPrChange", "sectPrChange", "tblPrChange",
                "trPrChange", "tcPrChange", "tblGridChange", "numberingChange")


def unwrap(element):
    parent = element.getparent()
    index = parent.index(element)
    for child in list(element):
        parent.insert(index, child)
        index += 1
    parent.remove(element)


def paragraph_mark_deleted(paragraph):
    rpr = paragraph.find(f"{q('w', 'pPr')}/{q('w', 'rPr')}")
    return rpr is not None and rpr.find(q("w", "del")) is not None


def accept(root):
    for tag in ("del", "moveFrom"):
        for element in list(root.iter(q("w", tag))):
            if element.getparent() is not None and element.getparent().tag != q("w", "rPr"):
                element.getparent().remove(element)
    for tag in ("ins", "moveTo"):
        for element in list(root.iter(q("w", tag))):
            parent = element.getparent()
            if parent is not None and parent.tag == q("w", "rPr"):
                parent.remove(element)  # 段落标记上的插入记号
            elif parent is not None:
                unwrap(element)
    for name in CHANGE_MARKS:
        for element in list(root.iter(q("w", name))):
            element.getparent().remove(element)
    for tag in ("moveFromRangeStart", "moveFromRangeEnd", "moveToRangeStart", "moveToRangeEnd"):
        for element in list(root.iter(q("w", tag))):
            element.getparent().remove(element)
    # 段落标记被删：本段剩下的内容并到下一段开头，本段消失。
    for paragraph in list(root.iter(q("w", "p"))):
        if not paragraph_mark_deleted(paragraph):
            continue
        following = paragraph.getnext()
        if following is None or following.tag != q("w", "p"):
            paragraph.find(f"{q('w', 'pPr')}/{q('w', 'rPr')}").remove(
                paragraph.find(f"{q('w', 'pPr')}/{q('w', 'rPr')}/{q('w', 'del')}")
            )
            continue
        content = [child for child in paragraph if child.tag != q("w", "pPr")]
        anchor = following.find(q("w", "pPr"))
        index = 0 if anchor is None else following.index(anchor) + 1
        for child in content:
            following.insert(index, child)
            index += 1
        paragraph.getparent().remove(paragraph)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source")
    parser.add_argument("output")
    args = parser.parse_args(argv)

    work = tempfile.mkdtemp(prefix="accept-")
    try:
        unpack(args.source, work)
        word = os.path.join(work, "word")
        for name in os.listdir(word):
            if name.endswith(".xml"):
                path = os.path.join(word, name)
                tree = etree.parse(path)
                accept(tree.getroot())
                tree.write(path, xml_declaration=True, encoding="UTF-8", standalone=True)
        pack(work, args.output)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(f"已写出 {args.output}：全部修订已接受")
    return 0


if __name__ == "__main__":
    sys.exit(main())
