"""删掉 pptx 里已经没人引用的幻灯片、备注页、图片和关系。

    python3 clean.py 已解压目录/

先把 <p:sldIdLst> 改到最终状态再跑：不在列表里的幻灯片会被当成已删除，
**连同你刚写好但还没登记进列表的那一页**。反复清理直到没有新的孤立部件。
"""

import os
import posixpath
import sys

from lxml import etree

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ooxml import NS, q  # noqa: E402

REL = f"{{{NS['rel']}}}Relationship"
SLIDE_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"
REMOVABLE = ("ppt/slides/", "ppt/notesSlides/", "ppt/media/", "ppt/charts/", "ppt/embeddings/")


def write(tree, path):
    tree.write(path, xml_declaration=True, encoding="UTF-8", standalone=True)


def rels_path_for(part):
    folder, name = posixpath.split(part)
    return posixpath.join(folder, "_rels", f"{name}.rels")


def all_parts(root):
    for base, _, files in os.walk(root):
        for file in files:
            yield posixpath.relpath(os.path.join(base, file), root).replace(os.sep, "/")


def remove_part(root, part, types):
    for name in (part, rels_path_for(part)):
        full = os.path.join(root, name)
        if os.path.exists(full):
            os.remove(full)
    for override in list(types.getroot()):
        if override.get("PartName", "").lstrip("/") == part:
            types.getroot().remove(override)


def drop_unlisted_slides(root, types):
    pres = etree.parse(os.path.join(root, "ppt", "presentation.xml"))
    listed = {el.get(q("r", "id")) for el in pres.getroot().iter(q("p", "sldId"))}
    rels_file = os.path.join(root, "ppt", "_rels", "presentation.xml.rels")
    rels = etree.parse(rels_file)
    removed = 0
    for rel in list(rels.getroot()):
        if rel.get("Type") == SLIDE_TYPE and rel.get("Id") not in listed:
            remove_part(root, posixpath.normpath(posixpath.join("ppt", rel.get("Target"))), types)
            rels.getroot().remove(rel)
            removed += 1
    write(rels, rels_file)
    return removed


def referenced(root):
    targets = set()
    for part in all_parts(root):
        if not part.endswith(".rels"):
            continue
        owner_dir = posixpath.dirname(posixpath.dirname(part))
        for rel in etree.parse(os.path.join(root, part)).getroot().iter(REL):
            if rel.get("TargetMode") == "External":
                continue
            target = rel.get("Target", "")
            targets.add(target.lstrip("/") if target.startswith("/")
                        else posixpath.normpath(posixpath.join(owner_dir, target)))
    return targets


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or not os.path.isdir(args[0]):
        print(__doc__)
        return 2
    root = args[0]
    types_path = os.path.join(root, "[Content_Types].xml")
    types = etree.parse(types_path)
    slides = drop_unlisted_slides(root, types)
    orphans = 0
    while True:
        # 关系文件本身失去主人时也要删：主人不在了，它指向的东西就没人引用了。
        for part in list(all_parts(root)):
            if part.endswith(".rels") and "/_rels/" in part:
                owner = posixpath.join(posixpath.dirname(posixpath.dirname(part)),
                                       posixpath.basename(part)[:-5])
                if not os.path.exists(os.path.join(root, owner)):
                    os.remove(os.path.join(root, part))
        live = referenced(root)
        dead = [p for p in all_parts(root)
                if p.startswith(REMOVABLE) and not p.endswith(".rels") and p not in live]
        if not dead:
            break
        for part in dead:
            remove_part(root, part, types)
        orphans += len(dead)
    write(types, types_path)
    print(f"删掉了 {slides} 页不在列表里的幻灯片、{orphans} 个没人引用的部件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
