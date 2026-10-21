"""LibreOffice 的封装：沙箱里直接跑 soffice 会卡住或抢配置，用这个。

    python3 soffice.py --headless --convert-to pdf 文件.docx [--outdir 目录]

参数原样交给 soffice，只多做三件事：每次一个独立的临时配置目录（沙箱家目录
不一定可写，两个转换同时跑也不会抢同一份配置锁）、超时（默认 180 秒，
SOFFICE_TIMEOUT 可改）、失败时把 LibreOffice 自己的输出打出来。
"""

import os
import shutil
import subprocess
import sys
import tempfile


def run(args, timeout=None):
    timeout = timeout or int(os.environ.get("SOFFICE_TIMEOUT", "180"))
    profile = tempfile.mkdtemp(prefix="lo-profile-")
    try:
        return subprocess.run(
            ["soffice", f"-env:UserInstallation=file://{profile}", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    finally:
        shutil.rmtree(profile, ignore_errors=True)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(__doc__)
        return 2
    try:
        result = run(args)
    except subprocess.TimeoutExpired:
        print("LibreOffice 超时没有返回。文件可能太大，或者本身已损坏。", file=sys.stderr)
        return 1
    output = (result.stdout + result.stderr).strip()
    if output:
        print(output)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
