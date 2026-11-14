"""把文档转成文字放进临时目录，打印字数、页数和目录：读文档技能用，云端对话与本机对话是同一个脚本。

    python3 extract_text.py 文件 <临时目录>

- PDF：有 pdftotext（云端沙箱）就用它，和会话附件的提取法一样（-layout 保留表格的列对齐）；没有（本机）就用 markitdown
  的 PDF 转换（pdfminer.six）。两者页与页之间都是换页符，换成「[第 n 页]」，和会话附件的文字同一个写法，回答第几页写了
  什么、用户核对都用得上。平均每页不到 50 个字的当作扫描件（和会话附件的判定一样），退出码 3；
- 图片要识别文字，直接退出码 3；
- .doc、.ppt、.xls 这类旧格式先经 soffice.py 转成新格式（云端是原生 LibreOffice，本机是官方 LibreOffice Kit）；
- 其余（Word、PPT、Excel 等）交给 markitdown。
结果写到 <临时目录>/<原文件名>.md，标准输出是给模型看的摘要：结果路径、字数、页数、目录（Markdown 标题，标题太少时
列每页开头，都带行号）。退出码：0 成功，2 用法或文件问题，3 提取不出文字（扫描件、图片），4 转换失败。
"""

import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import soffice  # noqa: E402

# 和会话附件的扫描件判定同一个数（业务服务 attachments/parse_job.py 的 SCANNED_CHARS_PER_PAGE）
SCANNED_CHARS_PER_PAGE = 50
PDFTOTEXT_TIMEOUT = 120
MAX_OUTLINE = 120
MAX_ENTRY = 80
MIN_HEADINGS = 3
IMAGES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".heic"}
# 旧格式先转成什么（官方 Kit 与原生 LibreOffice 都支持这几种）
UPGRADES = {".doc": "docx", ".wps": "docx", ".rtf": "docx", ".odt": "docx",
            ".ppt": "pptx", ".dps": "pptx", ".odp": "pptx",
            ".xls": "xlsx", ".et": "xlsx", ".ods": "xlsx"}
HEADING = re.compile(r"^#{1,6}\s+\S")
PAGE = re.compile(r"^\[第 (\d+) 页\]$")
BLANKS = re.compile(r"\n(?:[ \t]*\n){2,}")


class ExtractError(Exception):
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


def pdf_text(path):
    """PDF 的文字，页与页之间是换页符。"""
    if shutil.which("pdftotext"):
        done = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", path, "-"], capture_output=True,
                              timeout=PDFTOTEXT_TIMEOUT, check=False)
        if done.returncode != 0:
            raise ExtractError(f"PDF 打不开（可能已损坏或加了密码）：{done.stderr.decode(errors='replace')[-500:]}", 4)
        return done.stdout.decode("utf-8", errors="replace")
    return markitdown_text(path)


def markitdown_text(path):
    from markitdown import MarkItDown  # 延后导入：只用 pdftotext 的路径不需要它
    try:
        return MarkItDown(enable_plugins=False).convert(path).text_content
    except Exception as error:  # markitdown 各转换器抛的异常类型不统一
        raise ExtractError(f"转不成文字（文件可能已损坏、加了密码或格式不支持）：{error}", 4) from error


def paged(text):
    """换页符换成「[第 n 页]」（同会话附件），返回 (文字, 页数, 是不是扫描件)。"""
    pages = text.split("\f")
    if pages and not pages[-1].strip():
        pages = pages[:-1]
    count = max(1, len(pages))
    scanned = len("".join(text.split())) < SCANNED_CHARS_PER_PAGE * count
    # pdfminer 出来的空行很多：连续空行压成一个，少占模型的字数
    body = "\n\n".join(f"[第 {n} 页]\n{BLANKS.sub(chr(10) * 2, page).strip(chr(10))}" for n, page in enumerate(pages, 1))
    return body, len(pages), scanned


def upgraded(path, workdir):
    """旧格式经 soffice.py 转成新格式，放在临时目录里，返回新文件的路径。"""
    ext = os.path.splitext(path)[1].lower()
    target = UPGRADES[ext]
    outdir = os.path.join(workdir, ".converted")
    os.makedirs(outdir, exist_ok=True)
    try:
        result = soffice.convert(path, target, outdir)
    except (soffice.OfficeError, subprocess.TimeoutExpired) as error:
        raise ExtractError(f"旧格式转换没成功，请用户另存为 .{target} 再给你：{error}", 4) from error
    if not os.path.exists(result):
        raise ExtractError(f"旧格式转换没成功，请用户另存为 .{target} 再给你", 4)
    return result


def outline(text):
    """目录：Markdown 标题够多就列标题，否则列每页开头。每条是（行号，文字）。"""
    lines = text.split("\n")
    headings = [(n, line.strip()) for n, line in enumerate(lines, 1) if HEADING.match(line)]
    if len(headings) >= MIN_HEADINGS:
        return headings[:MAX_OUTLINE]
    entries = []
    for n, line in enumerate(lines, 1):
        found = PAGE.match(line)
        if not found:
            continue
        first = next((later.strip() for later in lines[n:n + 20] if later.strip()), "")
        entries.append((n, f"第 {found.group(1)} 页：{' '.join(first.split())}"))
    return (entries or headings)[:MAX_OUTLINE]


def free_name(workdir, stem):
    name = f"{stem}.md"
    index = 2
    while os.path.exists(os.path.join(workdir, name)):
        name = f"{stem}-{index}.md"
        index += 1
    return os.path.join(workdir, name)


def extract(path, workdir):
    """返回 (结果路径, 字数, 页数或 None, 目录)。"""
    if not os.path.isfile(path):
        raise ExtractError(f"找不到文件：{path}", 2)
    if not os.path.isdir(workdir):
        raise ExtractError(f"临时目录不存在：{workdir}", 2)
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGES:
        raise ExtractError("这是图片，要识别文字，这里提取不出来", 3)
    pages = None
    if ext == ".pdf":
        text, pages, scanned = paged(pdf_text(path))
        if scanned:
            raise ExtractError(f"这份 PDF 共 {pages} 页，几乎没有文字层，多半是扫描件，要识别文字，这里提取不出来", 3)
    else:
        source = upgraded(path, workdir) if ext in UPGRADES else path
        text = markitdown_text(source)
        if not text.strip():
            raise ExtractError("转出来没有文字（可能是空文件，或内容都是图片）", 3)
    result = free_name(workdir, os.path.splitext(os.path.basename(path))[0])
    with open(result, "w", encoding="utf-8") as handle:
        handle.write(text)
    return result, len(text), pages, outline(text)


def summary(result, chars, pages, entries):
    head = f"已转成文字：{result}（{chars} 字" + (f"，共 {pages} 页）" if pages else "）")
    if not entries:
        return head
    rows = "\n".join(f"  {line}: {text[:MAX_ENTRY]}" for line, text in entries)
    return f"{head}\n目录（行号: 内容）：\n{rows}"


def main(argv):
    if len(argv) != 2:
        print("用法：python3 extract_text.py 文件 <临时目录>", file=sys.stderr)
        return 2
    try:
        print(summary(*extract(argv[0], argv[1])))
    except ExtractError as error:
        print(str(error), file=sys.stderr)
        return error.code
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
