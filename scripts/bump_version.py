"""统一改版本号：python scripts/bump_version.py v0.0.2

版本只有一个来源，仓库根目录的 VERSION（带 v，与镜像标签、git 标签一致）。本脚本把它同步到：
- server/pyproject.toml 与 server/uv.lock 里本项目那一条（去掉 v）；
- web、runtime-host 的 package.json 与 package-lock.json 根上的版本（去掉 v）；
- deploy/docker-compose.yml 里 KNOWWORK_VERSION 的默认值、deploy/.env.example 的 KNOWWORK_VERSION（带 v）。
业务服务镜像构建时由发布流程传入 KNOWWORK_VERSION，系统设置页显示的就是它。
只做文本替换，不调用 uv、npm；每处都必须恰好命中，否则报错退出，免得漏改。
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def replace_once(path: Path, pattern: str, replacement: str, *, count: int = 1) -> None:
    text = path.read_text(encoding="utf-8")
    updated, found = re.subn(pattern, replacement, text, flags=re.MULTILINE)
    if found != count:
        raise SystemExit(f"{path.relative_to(ROOT)}：应命中 {count} 处，实际 {found} 处（{pattern}）")
    path.write_text(updated, encoding="utf-8")


def bump_package(directory: str, plain: str) -> None:
    package = ROOT / directory / "package.json"
    data = json.loads(package.read_text(encoding="utf-8"))
    package.write_text(json.dumps({**data, "version": plain}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lock = ROOT / directory / "package-lock.json"
    locked = json.loads(lock.read_text(encoding="utf-8"))
    packages = {**locked["packages"], "": {**locked["packages"][""], "version": plain}}
    lock.write_text(json.dumps({**locked, "version": plain, "packages": packages}, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def bump(tag: str) -> None:
    if not TAG.match(tag):
        raise SystemExit(f"版本号要写成 v主.次.修订，例如 v0.0.2，收到的是 {tag!r}")
    plain = tag[1:]
    (ROOT / "VERSION").write_text(tag + "\n", encoding="utf-8")
    replace_once(ROOT / "server/pyproject.toml", r'^version = "[^"]+"$', f'version = "{plain}"')
    replace_once(ROOT / "server/uv.lock", r'(^name = "knowwork-server"\nversion = )"[^"]+"', rf'\g<1>"{plain}"')
    for directory in ("web", "runtime-host"):
        bump_package(directory, plain)
    # compose 里每个我们自己的镜像都带一次版本默认值（server、runtime-host、两处 sandbox、gateway）
    replace_once(ROOT / "deploy/docker-compose.yml", r"\$\{KNOWWORK_VERSION:-[^}]+\}", f"${{KNOWWORK_VERSION:-{tag}}}",
                 count=5)
    replace_once(ROOT / "deploy/.env.example", r"^KNOWWORK_VERSION=.*$", f"KNOWWORK_VERSION={tag}")
    print(f"版本已改为 {tag}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__.splitlines()[0])
    bump(sys.argv[1])
