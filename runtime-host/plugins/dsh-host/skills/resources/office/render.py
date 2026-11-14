"""把 Word、Excel、PPT、PDF 的每一页渲染成 PNG，或者按页打出文字，用来核对成品的版式与内容。

    python3 render.py 文件 输出目录 [--pages 1,3]     每页一张 PNG（输出目录要还不存在或是空的），打印每张图的绝对路径
    python3 render.py 文件 --text                      按页打出文字（核对文字、顺序和分页）

引擎经 soffice.py（云端是原生 LibreOffice 加 poppler，本机是官方 LibreOffice Kit），两种执行环境都这样调。
打印出的路径交给 read_image 查看。
"""

import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import soffice  # noqa: E402


def pages_of(value):
    try:
        pages = sorted({int(part) for part in value.split(",") if part.strip()})
    except ValueError:
        pages = []
    if not pages or pages[0] < 1:
        raise SystemExit("--pages 写成从 1 起的页码，用逗号隔开，例如 1,3")
    return pages


def text_of(path):
    """按页的文字：有 pdftotext 就转成 PDF 后按页取（保留分页）；没有（本机）就按文档内容读，不分页。"""
    if shutil.which("pdftotext"):
        work = tempfile.mkdtemp(prefix="render-text-")
        try:
            pdf = path if path.lower().endswith(".pdf") else soffice.convert(path, "pdf", work)
            return subprocess.run(["pdftotext", "-layout", pdf, "-"], capture_output=True, text=True,
                                  check=True).stdout
        finally:
            shutil.rmtree(work, ignore_errors=True)
    try:
        from markitdown import MarkItDown
    except ImportError as error:
        raise soffice.OfficeError("这台电脑上没有 pdftotext，也没有 markitdown，读不了文字") from error
    return "（这台电脑上没有 pdftotext：下面按文档内容读出，不分页）\n" + MarkItDown().convert(path).text_content


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 2 and args[1] == "--text":
        try:
            print(text_of(args[0]))
        except (soffice.OfficeError, subprocess.SubprocessError) as error:
            print(f"没能读出文字：{error}", file=sys.stderr)
            return 1
        return 0
    if len(args) not in (2, 4) or (len(args) == 4 and args[2] != "--pages"):
        print(__doc__)
        return 2
    path, outdir = args[0], args[1]
    pages = pages_of(args[3]) if len(args) == 4 else None
    if not os.path.exists(path):
        print(f"文件不存在：{path}", file=sys.stderr)
        return 1
    if os.path.isdir(outdir) and os.listdir(outdir):
        print(f"输出目录 {outdir} 里已经有东西，换一个新的目录名", file=sys.stderr)
        return 2
    try:
        images = soffice.render_pages(path, outdir, pages=pages)
    except (soffice.OfficeError, subprocess.SubprocessError) as error:
        print(f"没能渲染：{error}", file=sys.stderr)
        return 1
    for image in images:
        print(os.path.abspath(image))
    return 0


if __name__ == "__main__":
    sys.exit(main())
