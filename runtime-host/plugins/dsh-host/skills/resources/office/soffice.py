"""Office 引擎的封装：转换、重算、出页面图都经这里，技能和别的脚本不直接调引擎。

    python3 soffice.py --headless --convert-to pdf 文件.docx [--outdir 目录]

两种执行环境各用各的引擎，入口只有这一个：
- 云端对话的沙箱里是原生 LibreOffice（soffice）。参数原样交给它，只多做三件事：每次一个独立的临时配置目录
  （沙箱家目录不一定可写，两个转换同时跑也不会抢同一份配置锁）、超时（默认 180 秒，SOFFICE_TIMEOUT 可改）、
  失败时把 LibreOffice 自己的输出打出来。业务服务的格式转换与公式重算也这样调它；
- 用户电脑上的本机会话是 DSH 官方的 LibreOffice Kit（dsoffice，和官方桌面版一样随桌面端带）。桌面端经官方的
  shell 环境给出它的位置（DSH_KNOWWORK_OFFICE_NODE、DSH_KNOWWORK_OFFICE_CLI）和字体回退分组
  （DSH_KNOWWORK_OFFICE_FONT_FALLBACKS，补上 mac 的中文字体，不然中文画成方框）；命令行认 --convert-to、--outdir
  和输入文件这几项（技能里用到的就是这些）。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

KIT_NODE = "DSH_KNOWWORK_OFFICE_NODE"
KIT_CLI = "DSH_KNOWWORK_OFFICE_CLI"
KIT_FONTS = "DSH_KNOWWORK_OFFICE_FONT_FALLBACKS"


class OfficeError(Exception):
    """引擎没能完成：message 可以直接给人看。"""


def _timeout(timeout=None):
    return timeout or int(os.environ.get("SOFFICE_TIMEOUT", "180"))


def kit():
    """官方 LibreOffice Kit 的 (node, cli)；这台机器上没有时是 None（用原生 LibreOffice）。"""
    node, cli = os.environ.get(KIT_NODE), os.environ.get(KIT_CLI)
    if node and cli and os.path.exists(node) and os.path.exists(cli):
        return node, cli
    return None


def run(args, timeout=None):
    """原生 LibreOffice：参数原样交给 soffice。"""
    profile = tempfile.mkdtemp(prefix="lo-profile-")
    try:
        return subprocess.run(
            ["soffice", f"-env:UserInstallation=file://{profile}", *args],
            capture_output=True,
            text=True,
            timeout=_timeout(timeout),
        )
    finally:
        shutil.rmtree(profile, ignore_errors=True)


def run_kit(args, timeout=None):
    """官方 LibreOffice Kit：dsoffice 的子命令，出错抛 OfficeError。桌面端给的 node 可能就是 Electron，
    要带 ELECTRON_RUN_AS_NODE 才按 Node 跑。"""
    node, cli = kit()
    fonts = os.environ.get(KIT_FONTS)
    result = subprocess.run(
        [node, cli, *args, *(["--font-fallbacks", fonts] if fonts else [])],
        capture_output=True,
        text=True,
        timeout=_timeout(timeout),
        env={**os.environ, "ELECTRON_RUN_AS_NODE": "1"},
    )
    if result.returncode != 0:
        raise OfficeError(_kit_error(result))
    return result


def _kit_error(result):
    output = (result.stdout + result.stderr).strip()
    try:
        reply = json.loads(output.splitlines()[-1])
        return str(reply.get("error") or reply.get("message") or output[:500])
    except (ValueError, IndexError, AttributeError):
        return output[:500] or "Office 引擎没能完成"


def _target(path, fmt, outdir):
    stem = os.path.splitext(os.path.basename(path))[0]
    return os.path.join(outdir, f"{stem}.{fmt.split(':', 1)[0]}")


def convert(path, fmt, outdir, timeout=None):
    """转成另一种格式（pdf、docx、xlsx、pptx、csv……）放进 outdir，返回生成的文件。fmt 可带原生 LibreOffice 的
    过滤器名（xlsx:Calc MS Excel 2007 XML）；官方 Kit 按后缀选格式，过滤器名不用。"""
    target = _target(path, fmt, outdir)
    if kit() is not None:
        run_kit(["convert", "--input", path, "--output", target], timeout)
        return target
    result = run(["--headless", "--convert-to", fmt, "--outdir", outdir, path], timeout)
    if not os.path.exists(target):
        raise OfficeError((result.stdout + result.stderr).strip()[:500] or "LibreOffice 没能打开这个文件")
    return target


def recalculate(path, output, timeout=None):
    """重算工作簿里的全部公式，写到 output：公式保留，缓存值换成新算的。"""
    if kit() is not None:
        run_kit(["recalculate", "--input", path, "--output", output], timeout)
        return output
    work = tempfile.mkdtemp(prefix="recalc-")
    try:
        shutil.move(convert(path, "xlsx:Calc MS Excel 2007 XML", work, timeout), output)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return output


def render_pages(path, outdir, *, dpi=100, pages=None, timeout=None):
    """每页（每张幻灯片）出一张 PNG 放进 outdir（还不存在、或是空的），返回按页序排好的路径。pages 是从 1 起的页码。"""
    if kit() is not None:
        if os.path.isdir(outdir) and not os.listdir(outdir):
            os.rmdir(outdir)  # 官方 Kit 要自己建这个目录
        args = ["render", "--input", path, "--output-dir", outdir]
        if pages:
            args += ["--pages", ",".join(str(page) for page in pages)]
        run_kit(args, timeout)
        return _kit_images(outdir)
    os.makedirs(outdir, exist_ok=True)
    work = tempfile.mkdtemp(prefix="render-")
    try:
        pdf = path if path.lower().endswith(".pdf") else convert(path, "pdf", work, timeout)
        for page in sorted(pages) if pages else [None]:
            limits = [] if page is None else ["-f", str(page), "-l", str(page)]
            subprocess.run(["pdftoppm", "-png", "-r", str(dpi), *limits, pdf, os.path.join(outdir, "page")],
                           check=True, capture_output=True, timeout=_timeout(timeout))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return sorted(os.path.join(outdir, name) for name in os.listdir(outdir) if name.endswith(".png"))


def _kit_images(outdir):
    """官方 Kit 在输出目录里写 manifest.json，images 按页序列出每张图的绝对路径。"""
    with open(os.path.join(outdir, "manifest.json"), encoding="utf-8") as handle:
        manifest = json.load(handle)
    return [image["path"] for image in sorted(manifest.get("images", []), key=lambda image: image.get("index", 0))]


def _cli_kit(args):
    """官方 Kit 下的命令行：只认 --convert-to、--outdir 和输入文件（--headless 这类原生参数不用管）。"""
    fmt, outdir, inputs, rest = None, os.getcwd(), [], list(args)
    while rest:
        arg = rest.pop(0)
        if arg == "--convert-to" and rest:
            fmt = rest.pop(0)
        elif arg == "--outdir" and rest:
            outdir = rest.pop(0)
        elif arg == "--headless":
            continue
        elif arg.startswith("-"):
            print(f"这台电脑上的 Office 引擎不认 {arg}，只支持 --convert-to、--outdir 和输入文件。", file=sys.stderr)
            return 2
        else:
            inputs.append(arg)
    if fmt is None or not inputs:
        print(__doc__)
        return 2
    os.makedirs(outdir, exist_ok=True)
    for path in inputs:
        print(convert(path, fmt, outdir))
    return 0


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(__doc__)
        return 2
    try:
        if kit() is not None:
            return _cli_kit(args)
        result = run(args)
    except subprocess.TimeoutExpired:
        print("Office 引擎超时没有返回。文件可能太大，或者本身已损坏。", file=sys.stderr)
        return 1
    except OfficeError as error:
        print(str(error), file=sys.stderr)
        return 1
    output = (result.stdout + result.stderr).strip()
    if output:
        print(output)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
