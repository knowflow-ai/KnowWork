"""把 pptx 每一页渲染成带页码的缩略图网格，用来挑模板版式。

    python3 thumbnail.py 文件.pptx 前缀

输出 前缀.jpg；超过 12 页时拆成 前缀-1.jpg、前缀-2.jpg……，并打印生成了哪些。
**一定要给前缀并用文件名命名它**：两份演示稿用同一个前缀，后一份会覆盖前一份。
这只用于挑版式；检查成品要看全尺寸渲染（render.py）。
"""

import os
import shutil
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import soffice  # noqa: E402

PER_GRID = 12
COLUMNS = 3
WIDTH = 400
# 默认字体画不出中文，标注会变成方块：云端沙箱里是思源黑体，本机用系统自带的中文字体
CJK_FONTS = ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/System/Library/Fonts/PingFang.ttc",
             "/System/Library/Fonts/Hiragino Sans GB.ttc", "C:\\Windows\\Fonts\\msyh.ttc")


def label_font():
    for path in CJK_FONTS:
        try:
            return ImageFont.truetype(path, 16)
        except OSError:
            continue
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
    work = tempfile.mkdtemp(prefix="thumbs-")
    try:
        try:
            pages = soffice.render_pages(path, os.path.join(work, "pages"), dpi=50, timeout=240)
        except (soffice.OfficeError, subprocess.SubprocessError) as error:
            print(f"Office 引擎没能渲染这份演示稿：{error}", file=sys.stderr)
            return 1
        if not pages:
            print("Office 引擎没能渲染这份演示稿", file=sys.stderr)
            return 1
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
