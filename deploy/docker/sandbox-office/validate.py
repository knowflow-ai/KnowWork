"""检查改过或生成的 docx / pptx / xlsx 能不能被 Office 正常打开。

    python3 validate.py 文件.docx
    python3 validate.py 输出.docx --original 原文件.docx --author "KnowWork Agent"
    python3 validate.py 文件.pptx --render

检查：每个 XML 部件都能解析；每个部件都在 [Content_Types].xml 里有类型；
每条内部关系指向的部件都存在；批注锚点与 comments.xml 对得上；修订标记带
作者和时间；pptx 的幻灯片列表都能找到对应文件。

--original 加 --author（仅 docx）：把该作者的修订全部拒绝后，正文应当与原文件
逐段一致；对不上的地方就是改了字却没包进 <w:ins>/<w:del> 的——在"接受后"的
视图里完全看不出来，最容易漏。
--render：再让 LibreOffice 真的打开并转成 PDF 一次。

发现问题时逐条列出并给出修法，退出码 1；没有问题退出码 0。
"""

import argparse
import copy
import os
import posixpath
import shutil
import subprocess
import sys
import tempfile
import zipfile

from lxml import etree

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ooxml import NS, q  # noqa: E402

RELS_NS = NS["rel"]


def read_parts(path, problems):
    parts = {}
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        problems.append("不是有效的压缩包。修法：从解压目录里面重新打包（zip -Xr）。")
        return parts
    with archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                problems.append(f"{info.filename} 是符号链接。修法：删掉后重新打包。")
                continue
            parts[info.filename] = archive.read(info.filename)
    return parts


def parse_xml(parts, problems):
    trees = {}
    for name, data in parts.items():
        if name.endswith((".xml", ".rels")):
            try:
                trees[name] = etree.fromstring(data)
            except etree.XMLSyntaxError as error:
                problems.append(f"{name} 不是合法的 XML：{error}。修法：检查手改处的标签是否成对、特殊字符是否转义。")
    return trees


def check_content_types(parts, trees, problems, notes):
    types = trees.get("[Content_Types].xml")
    if types is None:
        problems.append("缺少 [Content_Types].xml。修法：确认是从解压目录里面打包的。")
        return
    defaults = {el.get("Extension", "").lower() for el in types.iter(q("ct", "Default"))}
    overrides = {el.get("PartName", "").lstrip("/") for el in types.iter(q("ct", "Override"))}
    for name in parts:
        if name == "[Content_Types].xml":
            continue
        extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if name not in overrides and extension not in defaults:
            problems.append(f"{name} 在 [Content_Types].xml 里没有类型。修法：加一条 Override。")
    # 反过来——声明了不存在的部件——Office 直接忽略，pptxgenjs 生成的文件里就常有
    # （为没用到的母版预先声明类型）。当成错误会让人去修一个不存在的问题。
    stale = sorted(name for name in overrides if name not in parts)
    if stale:
        notes.append(f"[Content_Types].xml 声明了 {len(stale)} 个不存在的部件（Office 会忽略，不用修）")


def check_relationships(parts, trees, problems):
    for name, root in trees.items():
        if not name.endswith(".rels"):
            continue
        folder = posixpath.dirname(posixpath.dirname(name))
        for rel in root.iter(f"{{{RELS_NS}}}Relationship"):
            if rel.get("TargetMode") == "External":
                continue
            target = rel.get("Target", "")
            resolved = target.lstrip("/") if target.startswith("/") else posixpath.normpath(
                posixpath.join(folder, target)
            )
            if resolved not in parts:
                problems.append(f"{name} 的关系 {rel.get('Id')} 指向不存在的 {resolved}。修法：补上该部件或删掉这条关系。")


def check_docx(trees, problems):
    document = trees.get("word/document.xml")
    if document is None:
        problems.append("缺少 word/document.xml。")
        return
    comments = trees.get("word/comments.xml")
    known = set() if comments is None else {
        el.get(q("w", "id")) for el in comments.iter(q("w", "comment"))
    }
    for tag in ("commentRangeStart", "commentReference"):
        for el in document.iter(q("w", tag)):
            if el.get(q("w", "id")) not in known:
                problems.append(f"正文里的批注 {el.get(q('w', 'id'))} 在 comments.xml 里没有。修法：用 comment.py 新建批注，用它打印的编号。")
    starts = {el.get(q("w", "id")) for el in document.iter(q("w", "commentRangeStart"))}
    ends = {el.get(q("w", "id")) for el in document.iter(q("w", "commentRangeEnd"))}
    for missing in sorted(starts ^ ends):
        problems.append(f"批注 {missing} 的起止标记不成对。修法：commentRangeStart 与 commentRangeEnd 都要有。")
    for tag in ("ins", "del"):
        for el in document.iter(q("w", tag)):
            if not el.get(q("w", "author")) or not el.get(q("w", "date")):
                problems.append(f"有 <w:{tag}> 缺少 w:author 或 w:date。修法：补上这两个属性和 w:id。")
                break
    for el in document.iter(q("w", "del")):
        if el.find(f".//{q('w', 't')}") is not None:
            problems.append("<w:del> 里用了 <w:t>。修法：删除内容里的文字元素必须是 <w:delText>。")
            break


def check_pptx(parts, trees, problems):
    presentation = trees.get("ppt/presentation.xml")
    rels = trees.get("ppt/_rels/presentation.xml.rels")
    if presentation is None or rels is None:
        problems.append("缺少 ppt/presentation.xml 或它的关系文件。")
        return
    targets = {r.get("Id"): r.get("Target") for r in rels.iter(f"{{{RELS_NS}}}Relationship")}
    for slide in presentation.iter(q("p", "sldId")):
        rid = slide.get(q("r", "id"))
        target = targets.get(rid)
        if target is None:
            problems.append(f"幻灯片列表里的 {rid} 在关系文件里没有。修法：用 add_slide.py 加页，不要手抄。")
        elif posixpath.normpath(posixpath.join("ppt", target)) not in parts:
            problems.append(f"幻灯片 {target} 文件不存在。修法：从 <p:sldIdLst> 里删掉它，再跑 clean.py。")


def paragraph_texts(document, reject_author=None):
    root = copy.deepcopy(document)
    if reject_author is not None:
        for el in list(root.iter(q("w", "ins"))):
            # 拒绝插入：连同段落标记上的插入记号一起去掉。
            if el.get(q("w", "author")) == reject_author and el.getparent() is not None:
                el.getparent().remove(el)
        for el in list(root.iter(q("w", "del"))):
            if el.get(q("w", "author")) != reject_author or el.getparent() is None:
                continue
            if el.getparent().tag == q("w", "rPr"):
                el.getparent().remove(el)
                continue
            for text in el.iter(q("w", "delText")):
                text.tag = q("w", "t")
            parent, index = el.getparent(), el.getparent().index(el)
            for child in list(el):
                parent.insert(index, child)
                index += 1
            parent.remove(el)
    texts = []
    body = root.find(q("w", "body"))
    for paragraph in body.iter(q("w", "p")):
        text = "".join(t.text or "" for t in paragraph.iter(q("w", "t")))
        if text.strip():
            texts.append(text)
    return texts


def check_tracked(trees, original_path, author, problems):
    original = read_parts(original_path, problems)
    source = original.get("word/document.xml")
    if source is None:
        problems.append("--original 不是 docx。")
        return
    before = paragraph_texts(etree.fromstring(source))
    after = paragraph_texts(trees["word/document.xml"], reject_author=author)
    if before == after:
        return
    shown = 0
    for index in range(max(len(before), len(after))):
        old = before[index] if index < len(before) else "（无）"
        new = after[index] if index < len(after) else "（无）"
        if old != new:
            problems.append(f"第 {index + 1} 段有没包进修订的改动：原文「{old[:80]}」→ 现在「{new[:80]}」。修法：把改动包进 <w:ins>/<w:del>（作者 {author}）。")
            shown += 1
            if shown >= 10:
                problems.append("……还有更多，先修上面这些。")
                break


def check_render(path, problems):
    out = tempfile.mkdtemp(prefix="validate-")
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        result = subprocess.run(
            [sys.executable, os.path.join(here, "soffice.py"), "--headless",
             "--convert-to", "pdf", "--outdir", out, path],
            capture_output=True, text=True, timeout=240,
        )
        if not any(name.endswith(".pdf") for name in os.listdir(out)):
            problems.append(f"LibreOffice 打不开这个文件：{(result.stdout + result.stderr).strip()[:300]}")
    finally:
        shutil.rmtree(out, ignore_errors=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path")
    parser.add_argument("--original", help="改之前的原文件（docx 修订检查用）")
    parser.add_argument("--author", help="你写修订时用的作者名")
    parser.add_argument("--render", action="store_true", help="再用 LibreOffice 打开一次")
    args = parser.parse_args(argv)

    problems, notes = [], []
    parts = read_parts(args.path, problems)
    trees = parse_xml(parts, problems)
    if parts:
        check_content_types(parts, trees, problems, notes)
        check_relationships(parts, trees, problems)
        kind = args.path.lower().rsplit(".", 1)[-1]
        if kind in ("docx", "dotx"):
            check_docx(trees, problems)
            if args.original and args.author and "word/document.xml" in trees:
                check_tracked(trees, args.original, args.author, problems)
        elif kind in ("pptx", "potx"):
            check_pptx(parts, trees, problems)
        if args.render and not problems:
            check_render(args.path, problems)

    for note in notes:
        print(f"提示：{note}")
    if problems:
        print(f"发现 {len(problems)} 个问题：")
        for problem in problems:
            print(f"- {problem}")
        return 1
    print("检查通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
