"""给 docx 新建一条批注，并打印把它锚到正文所需的标记。

    python3 comment.py 已解压目录/ "批注内容" [--author 名字]
    python3 comment.py 文件.docx "批注内容" -o 输出.docx [--author 名字]

批注要几个互相引用的部件（comments.xml、关系、内容类型）一起对上，手写很容易
漏一个。本脚本把这些都建好、分配批注编号，然后打印三段标记：把 commentRangeStart
放在要批注的文字之前、commentRangeEnd 放在之后、紧跟着放 commentReference 那个
run。**标记放进 document.xml 之前，批注存在但看不见。**
"""

import argparse
import os
import shutil
import sys
import tempfile

from docx import Document

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ooxml import pack, unpack  # noqa: E402


def markers(comment_id):
    return (
        f'<w:commentRangeStart w:id="{comment_id}"/>\n'
        f'<w:commentRangeEnd w:id="{comment_id}"/>\n'
        f'<w:r><w:rPr><w:rStyle w:val="CommentReference"/></w:rPr>'
        f'<w:commentReference w:id="{comment_id}"/></w:r>'
    )


def add(docx_path, output, text, author):
    doc = Document(docx_path)
    comment = doc.comments.add_comment(text=text, author=author, initials=author[:2])
    doc.save(output)
    return comment.comment_id


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", help="已解压的目录或 .docx")
    parser.add_argument("text")
    parser.add_argument("--author", default="KnowWork Agent")
    parser.add_argument("-o", "--output", help="输入是 .docx 时写到这里")
    args = parser.parse_args(argv)

    if os.path.isdir(args.path):
        scratch = tempfile.mkdtemp(prefix="comment-")
        try:
            packed = os.path.join(scratch, "in.docx")
            pack(args.path, packed)
            comment_id = add(packed, packed, args.text, args.author)
            fresh = os.path.join(scratch, "out")
            unpack(packed, fresh)
            for name in os.listdir(args.path):
                target = os.path.join(args.path, name)
                shutil.rmtree(target) if os.path.isdir(target) else os.remove(target)
            for name in os.listdir(fresh):
                shutil.move(os.path.join(fresh, name), args.path)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
    else:
        if not args.output:
            parser.error("输入是 .docx 时要用 -o 指定输出文件")
        comment_id = add(args.path, args.output, args.text, args.author)

    print(f"已建批注 id={comment_id}。把下面三段放进 word/document.xml：")
    print(markers(comment_id))
    return 0


if __name__ == "__main__":
    sys.exit(main())
