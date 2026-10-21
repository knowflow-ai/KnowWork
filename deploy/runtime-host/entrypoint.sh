#!/bin/sh
# 运行时宿主容器入口：
# 1. 从 bootstrap 生成的只读文件读取宿主令牌（不进 compose 环境变量，不需要用户手填）；
# 2. 按测试支撑同样的方式在 $DSH_HOME 下装配配置档：拷贝 profile 模板，KnowWork 插件以软链接入；
#    每次启动整体替换，镜像升级后配置档随之更新（会话数据在 $DSH_HOME 的其他目录，不受影响）；
# 3. 叠加容器部署层补丁后以前台进程运行 dsh。
set -eu

APP_DIR="${KNOWWORK_APP_DIR:-/app}"
DSH_HOME="${DSH_HOME:-/data/dsh}"
PROFILE_NAME="knowwork-host"
TOKEN_FILE="${KNOWWORK_HOST_TOKEN_FILE:-/run/knowwork/host/host_token}"
DEPLOY_PATCH="${KNOWWORK_DEPLOY_PATCH:-$APP_DIR/deploy.patch.yml}"

fail() {
  echo "knowwork-runtime-host: $1" >&2
  exit 1
}

[ -r "$TOKEN_FILE" ] || fail "无法读取宿主令牌文件（bootstrap 是否已运行？）"
KNOWWORK_HOST_TOKEN="$(cat "$TOKEN_FILE")"
[ "${#KNOWWORK_HOST_TOKEN}" -ge 32 ] || fail "宿主令牌过短"
export KNOWWORK_HOST_TOKEN DSH_HOME

profile="$DSH_HOME/profiles/$PROFILE_NAME"
staging="$profile.staging.$$"
mkdir -p "$DSH_HOME/profiles"
rm -rf "$staging"
mkdir -p "$staging/node_modules/@knowwork"
for file in package.json cordis.yml cordis.patch.yml pnpm-workspace.yaml; do
  cp "$APP_DIR/profile/$file" "$staging/$file"
done
ln -s "$APP_DIR/plugins/dsh-host" "$staging/node_modules/@knowwork/dsh-host"
ln -s "$APP_DIR/plugins/host-bundle" "$staging/node_modules/@knowwork/dsh-host-bundle"
rm -rf "$profile"
mv "$staging" "$profile"

cd "$APP_DIR"
exec "$APP_DIR/node_modules/.bin/dsh" --profile "$PROFILE_NAME" --patch "$DEPLOY_PATCH" "$@"
