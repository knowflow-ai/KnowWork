#!/usr/bin/env bash
# shellcheck source-path=SCRIPTDIR
# 改完补丁后重新导出：export.sh <GenOffice 目录>
# 在那个目录的 knowwork 分支上改（一个主题一个提交，见 patches/ 现有的四个），再跑这个；补丁文件整组重写。
# 导出时去掉提交号与签名行，内容没变的补丁导出来也不变，评审时只看得到真正的改动。
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=UPSTREAM
. "$here/UPSTREAM"
src=${1:?用法：export.sh <GenOffice 目录>}
git -C "$src" merge-base --is-ancestor "$commit" knowwork || {
  echo "knowwork 分支不在 UPSTREAM 的提交 ${commit:0:8} 上：升级基点时先改 UPSTREAM" >&2
  exit 1
}
rm -f "$here"/patches/*.patch
git -C "$src" format-patch --quiet --zero-commit --no-signature -o "$here/patches" "$commit..knowwork"
ls "$here/patches"
