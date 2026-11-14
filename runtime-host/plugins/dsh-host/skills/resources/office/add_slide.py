"""复制一页幻灯片（或从某个版式新建一页），把包里要登记的地方都登记好。

    python3 add_slide.py 已解压目录/ slide2.xml [--after slide5.xml]
    python3 add_slide.py 已解压目录/ slideLayout3.xml
    python3 add_slide.py 文件.pptx slide2.xml -o 输出.pptx

一页新幻灯片要同时出现在：ppt/slides/ 下的文件与它的关系文件、
[Content_Types].xml、presentation.xml 的关系文件、<p:sldIdLst>。手抄文件会漏，
PowerPoint 就报文件损坏。复制时不带走原页的备注页（备注是每页自己的）；图表、
SmartArt、嵌入对象仍然**引用**原页的同一个部件，改其中一页的图表另一页也会变。
输入是 .pptx 时必须给 -o，否则直接改原文件。
"""

import argparse
import os
import re
import shutil
import sys

from lxml import etree

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ooxml import NS, Workdir, q  # noqa: E402

REL = f"{{{NS['rel']}}}Relationship"
SLIDE_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"
LAYOUT_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout"
NOTES_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide"
SLIDE_CONTENT = "application/vnd.openxmlformats-officedocument.presentationml.slide+xml"


def parse(path):
    return etree.parse(path, etree.XMLParser(remove_blank_text=False))


def write(tree, path):
    tree.write(path, xml_declaration=True, encoding="UTF-8", standalone=True)


def next_slide_number(slides_dir):
    numbers = [int(m.group(1)) for name in os.listdir(slides_dir)
               if (m := re.fullmatch(r"slide(\d+)\.xml", name))]
    return max(numbers, default=0) + 1


def slide_from_layout(root, layout_name):
    """版式里的占位符照搬成空白页，文字清空。"""

    layout = parse(os.path.join(root, "ppt", "slideLayouts", layout_name)).getroot()
    tree = layout.find(f"{q('p', 'cSld')}/{q('p', 'spTree')}")
    sld = etree.Element(q("p", "sld"), nsmap={"p": NS["p"], "a": NS["a"], "r": NS["r"]})
    csld = etree.SubElement(sld, q("p", "cSld"))
    new_tree = etree.SubElement(csld, q("p", "spTree"))
    for child in tree:
        if child.tag in (q("p", "nvGrpSpPr"), q("p", "grpSpPr")):
            new_tree.append(etree.fromstring(etree.tostring(child)))
        elif child.tag == q("p", "sp") and child.find(f".//{q('p', 'ph')}") is not None:
            shape = etree.fromstring(etree.tostring(child))
            body = shape.find(q("p", "txBody"))
            if body is not None:
                for paragraph in body.findall(q("a", "p"))[1:]:
                    body.remove(paragraph)
                for run in body.iter(q("a", "r")):
                    run.getparent().remove(run)
            new_tree.append(shape)
    etree.SubElement(sld, q("p", "clrMapOvr")).append(etree.Element(q("a", "masterClrMapping")))
    return etree.ElementTree(sld)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", help="已解压的目录或 .pptx")
    parser.add_argument("source", help="slideN.xml 或 slideLayoutN.xml")
    parser.add_argument("--after", help="放在哪一页之后，如 slide5.xml（默认放最后）")
    parser.add_argument("-o", "--output")
    args = parser.parse_args(argv)
    if not os.path.isdir(args.path) and not args.output:
        parser.error("输入是 .pptx 时要用 -o 指定输出，否则会改掉原文件")

    with Workdir(args.path, args.output) as work:
        root = work.dir
        slides_dir = os.path.join(root, "ppt", "slides")
        rels_dir = os.path.join(slides_dir, "_rels")
        os.makedirs(rels_dir, exist_ok=True)
        number = next_slide_number(slides_dir)
        new_name = f"slide{number}.xml"

        if args.source.startswith("slideLayout"):
            write(slide_from_layout(root, args.source), os.path.join(slides_dir, new_name))
            rels = etree.Element(f"{{{NS['rel']}}}Relationships", nsmap={None: NS["rel"]})
            etree.SubElement(rels, REL, Id="rId1", Type=LAYOUT_TYPE,
                             Target=f"../slideLayouts/{args.source}")
            write(etree.ElementTree(rels), os.path.join(rels_dir, f"{new_name}.rels"))
        else:
            source = os.path.join(slides_dir, args.source)
            if not os.path.exists(source):
                raise SystemExit(f"找不到 ppt/slides/{args.source}")
            shutil.copyfile(source, os.path.join(slides_dir, new_name))
            source_rels = os.path.join(rels_dir, f"{args.source}.rels")
            if os.path.exists(source_rels):
                tree = parse(source_rels)
                for rel in list(tree.getroot()):
                    if rel.get("Type") == NOTES_TYPE:
                        tree.getroot().remove(rel)
                write(tree, os.path.join(rels_dir, f"{new_name}.rels"))

        types_path = os.path.join(root, "[Content_Types].xml")
        types = parse(types_path)
        etree.SubElement(types.getroot(), q("ct", "Override"),
                         PartName=f"/ppt/slides/{new_name}", ContentType=SLIDE_CONTENT)
        write(types, types_path)

        pres_rels_path = os.path.join(root, "ppt", "_rels", "presentation.xml.rels")
        pres_rels = parse(pres_rels_path)
        used = {rel.get("Id") for rel in pres_rels.getroot()}
        rid = next(f"rId{i}" for i in range(1, 10000) if f"rId{i}" not in used)
        etree.SubElement(pres_rels.getroot(), REL, Id=rid, Type=SLIDE_TYPE, Target=f"slides/{new_name}")
        write(pres_rels, pres_rels_path)

        pres_path = os.path.join(root, "ppt", "presentation.xml")
        pres = parse(pres_path)
        id_list = pres.getroot().find(q("p", "sldIdLst"))
        if id_list is None:
            raise SystemExit("presentation.xml 里没有 <p:sldIdLst>")
        new_id = max([int(el.get("id")) for el in id_list] + [255]) + 1
        entry = etree.Element(q("p", "sldId"), id=str(new_id))
        entry.set(q("r", "id"), rid)
        position = len(id_list)
        if args.after:
            targets = {rel.get("Id"): rel.get("Target") for rel in pres_rels.getroot()}
            for index, el in enumerate(id_list):
                if targets.get(el.get(q("r", "id"))) == f"slides/{args.after}":
                    position = index + 1
        id_list.insert(position, entry)
        write(pres, pres_path)

    print(f"Created ppt/slides/{new_name} from {args.source}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
