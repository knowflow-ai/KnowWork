"""把 pptx 每一页渲染成带页码的缩略图网格，用来挑模板版式。

    python3 thumbnail.py 文件.pptx 前缀

输出 前缀.jpg；超过 12 页时拆成 前缀-1.jpg、前缀-2.jpg……，并打印生成了哪些。
**一定要给前缀并用文件名命名它**：两份演示稿用同一个前缀，后一份会覆盖前一份。
这只用于挑版式；检查成品要看全尺寸渲染（soffice 转 PDF 再 pdftoppm）。
"""

import glob
import os
import shutil
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFont

PER_GRID = 12
COLUMNS = 3
WIDTH = 400
CJK_FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"


def label_font():
    # 默认字体画不出中文，标注会变成方块。
    try:
        return ImageFont.truetype(CJK_FONT, 16)
    except OSError:
        return ImageFont.load_default()


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print(__doc__)
        return 2
    path, prefix = args
    if not path.lower().endswith(".pptx"):
        print("只接受 .pptx；.potx 先复制成 .pptx 的名字。", file=sys.stderr)
        return 2
    here = os.path.dirname(os.path.abspath(__file__))
    work = tempfile.mkdtemp(prefix="thumbs-")
    try:
        subprocess.run(
            [sys.executable, os.path.join(here, "soffice.py"), "--headless",
             "--convert-to", "pdf", "--outdir", work, path],
            check=True, capture_output=True, timeout=240,
        )
        pdf = glob.glob(os.path.join(work, "*.pdf"))
        if not pdf:
            print("LibreOffice 没能渲染这份演示稿", file=sys.stderr)
            return 1
        subprocess.run(["pdftoppm", "-jpeg", "-r", "50", pdf[0], os.path.join(work, "p")],
                       check=True, timeout=240)
        pages = sorted(glob.glob(os.path.join(work, "p-*.jpg")))
        written = []
        for start in range(0, len(pages), PER_GRID):
            chunk = pages[start:start + PER_GRID]
            images = [Image.open(page) for page in chunk]
            scale = WIDTH / images[0].width
            cell_h = int(images[0].height * scale) + 24
            rows = (len(images) + COLUMNS - 1) // COLUMNS
            grid = Image.new("RGB", (COLUMNS * (WIDTH + 12) + 12, rows * (cell_h + 12) + 12), "white")
            draw = ImageDraw.Draw(grid)
            font = label_font()
            for offset, image in enumerate(images):
                x = 12 + (offset % COLUMNS) * (WIDTH + 12)
                y = 12 + (offset // COLUMNS) * (cell_h + 12)
                grid.paste(image.resize((WIDTH, cell_h - 24)), (x, y + 24))
                draw.text((x, y + 4), f"第 {start + offset + 1} 页", fill="black", font=font)
            name = f"{prefix}.jpg" if len(pages) <= PER_GRID else f"{prefix}-{start // PER_GRID + 1}.jpg"
            grid.save(name, quality=85)
            written.append(name)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print("生成了：" + "、".join(written))
    return 0


if __name__ == "__main__":
    sys.exit(main())
