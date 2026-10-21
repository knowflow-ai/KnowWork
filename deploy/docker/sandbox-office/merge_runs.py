"""合并 docx 里格式相同、紧挨着的文字片段，让看得见的一句话在 XML 里也连续。

    python3 merge_runs.py 已解压目录/
    python3 merge_runs.py 文件.docx -o 合并后.docx

Word 会因为修订号、拼写检查标记把一句话拆成很多 <w:r>，于是在 document.xml
里搜不到这句原文。本脚本只合并格式（<w:rPr>）完全相同的相邻片段，去掉拼写
检查标记 <w:proofErr> 和片段上的修订号属性（w:rsid*）；内容与渲染都不变。
处理 document.xml 以及页眉、页脚、脚注、尾注。
"""

import argparse
import copy
import glob
import os
import sys

from lxml import etree

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ooxml import Workdir, q  # noqa: E402

RUN, TEXT, RPR = q("w", "r"), q("w", "t"), q("w", "rPr")
CONTAINERS = {q("w", "p"), q("w", "hyperlink"), q("w", "ins"), q("w", "del"),
              q("w", "smartTag"), q("w", "fldSimple")}


RSID_PREFIX = q("w", "rsid")


def strip_rsid(element):
    for name in list(element.attrib):
        if name.startswith(RSID_PREFIX):
            del element.attrib[name]


def format_key(run):
    rpr = run.find(RPR)
    if rpr is None:
        return b""
    rpr = copy.deepcopy(rpr)
    for node in rpr.iter():
        strip_rsid(node)
    return etree.tostring(rpr, method="c14n")


def is_plain_text(run):
    """只有 rPr 和 w:t 的片段才能安全合并；带制表符、图片、域代码的不动。"""

    return all(child.tag in (RPR, TEXT) for child in run) and run.find(TEXT) is not None


def merge_container(container):
    merged = 0
    for proof in container.findall(q("w", "proofErr")):
        container.remove(proof)
    previous = None
    for child in list(container):
        if child.tag in CONTAINERS:
            merged += merge_container(child)
            previous = None
            continue
        if child.tag != RUN:
            previous = None
            continue
        strip_rsid(child)
        if not is_plain_text(child):
            previous = None
            continue
        if previous is not None and format_key(previous) == format_key(child):
            target = previous.find(TEXT)
            target.text = (target.text or "") + "".join(
                node.text or "" for node in child.findall(TEXT)
            )
            target.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            for extra in previous.findall(TEXT)[1:]:
                target.text += extra.text or ""
                previous.remove(extra)
            container.remove(child)
            merged += 1
            continue
        previous = child
    return merged


def merge_file(path):
    tree = etree.parse(path)
    merged = sum(merge_container(p) for p in tree.getroot().iter(q("w", "p")))
    tree.write(path, xml_declaration=True, encoding="UTF-8", standalone=True)
    return merged


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", help="已解压的目录或 .docx")
    parser.add_argument("-o", "--output", help="输入是 .docx 时写到这里（不给就原地改）")
    args = parser.parse_args(argv)

    with Workdir(args.path, args.output) as work:
        parts = [os.path.join(work.dir, "word", "document.xml")]
        for pattern in ("header*.xml", "footer*.xml", "footnotes.xml", "endnotes.xml"):
            parts += glob.glob(os.path.join(work.dir, "word", pattern))
        total = sum(merge_file(part) for part in parts if os.path.exists(part))
    print(f"合并了 {total} 个片段")
    return 0


if __name__ == "__main__":
    sys.exit(main())
