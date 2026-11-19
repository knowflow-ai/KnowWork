#!/usr/bin/env bash
# shellcheck source-path=SCRIPTDIR
# 补丁还打不打得上上游最新的代码：check-upstream.sh <GenOffice 目录> [上游 ref，默认 main]
# 我们的基点是「上游 + 网页模式 PR」（UPSTREAM 的 ref），所以先把那个 PR 合进给定的上游版本，再试打我们的补丁；
# 给的就是那个 PR 的 ref 时直接在它上面试。分开报：PR 自己和上游冲突（退出码 2），还是我们的补丁打不上（退出码 1）。
# 只在临时分支上试，不改 UPSTREAM、不动 knowwork 分支。真升级时照这个结果把 knowwork 分支换到新基点上解决冲突，
# 再 export.sh、改 UPSTREAM 的 commit。
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=UPSTREAM
. "$here/UPSTREAM"
dir=${1:?用法：check-upstream.sh <GenOffice 目录> [上游 ref]}
target=${2:-main}
cd "$dir"
git fetch --quiet "$repository" "$target"
base=$(git rev-parse FETCH_HEAD)
git fetch --quiet "$repository" "$ref"
pr=$(git rev-parse FETCH_HEAD)
branch="knowwork-check-$$"
git checkout --quiet -B "$branch" "$base"
cleanup() {
  git am --abort >/dev/null 2>&1 || true
  git merge --abort >/dev/null 2>&1 || true
  git checkout --quiet --detach >/dev/null 2>&1 || true
  git branch --quiet -D "$branch" >/dev/null 2>&1 || true
}
trap cleanup EXIT
if [ "$base" != "$pr" ] && ! git merge-base --is-ancestor "$pr" "$base"; then
  if ! git -c user.name=knowwork -c user.email=knowwork@localhost merge --quiet --no-edit "$pr" >/dev/null 2>&1; then
    echo "网页模式 PR（${pr:0:8}）合不进 ${target}（${base:0:8}），冲突的文件：" >&2
    git diff --name-only --diff-filter=U >&2 || true
    exit 2
  fi
fi
if git -c user.name=knowwork -c user.email=knowwork@localhost am --quiet --3way "$here"/patches/*.patch >/dev/null 2>&1; then
  echo "补丁全部打得上 ${target}（${base:0:8}）+ 网页模式 PR（${pr:0:8}）"
else
  subject=$(git am --show-current-patch=raw 2>/dev/null | sed -n 's/^Subject: \(\[PATCH[^]]*\] \)\{0,1\}//p' | head -1)
  echo "打不上 ${target}（${base:0:8}）：卡在「${subject}」，冲突的文件：" >&2
  git diff --name-only --diff-filter=U >&2 || true
  exit 1
fi
