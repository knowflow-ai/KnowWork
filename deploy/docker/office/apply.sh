#!/usr/bin/env bash
# shellcheck source-path=SCRIPTDIR
# 把 GenOffice 准备成「固定版本 + 我们的补丁」：apply.sh <GenOffice 目录>
# 目录不存在就克隆；已有就在里面重建 knowwork 分支（只动这个分支）。桌面安装包从这样准备好的源码编译；
# Office 镜像（deploy/docker/office.Dockerfile）在构建里照同样的做法取源码、打补丁。
# 补丁打不上（上游在同一处有改动）就停在那个补丁，git am 的提示里写着怎么继续。
# GENOFFICE_REPO 可以指到本机已有的克隆，免得每次从网上拉。
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=UPSTREAM
. "$here/UPSTREAM"
dest=${1:?用法：apply.sh <GenOffice 目录>}
source_repo=${GENOFFICE_REPO:-$repository}
if [ ! -d "$dest/.git" ]; then
  git clone --quiet "$source_repo" "$dest"
fi
cd "$dest"
# 固定的提交在 PR 的分支上，默认克隆不带它
if ! git cat-file -e "$commit^{commit}" 2>/dev/null; then
  git fetch --quiet "$source_repo" "$ref"
fi
git checkout --quiet -B knowwork "$commit"
git -c user.name=knowwork -c user.email=knowwork@localhost am --quiet --3way "$here"/patches/*.patch
echo "GenOffice ${commit:0:8} + $(find "$here/patches" -name '*.patch' | wc -l | tr -d ' ') 个补丁，分支 knowwork"
