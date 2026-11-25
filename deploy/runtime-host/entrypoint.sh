#!/bin/sh
# 运行时宿主容器入口：
# 1. 从 bootstrap 生成的只读文件读取宿主令牌（不进 compose 环境变量，不需要用户手填）；
# 2. 按测试支撑同样的方式在 $DSH_HOME 下装配配置档：拷贝 profile 模板，KnowWork 插件以软链接入，扩展的镜像多挂的配置层
#    （KNOWWORK_HOST_BUNDLES）排在后面；个人专属宿主另外装上传的插件（KNOWWORK_HOST_PLUGIN_DIR）；
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

# 个人专属宿主（业务服务按人建的容器）直接经环境变量给这一个容器现生成的令牌；共享宿主从 bootstrap 的文件读
if [ -z "${KNOWWORK_HOST_TOKEN:-}" ]; then
  [ -r "$TOKEN_FILE" ] || fail "无法读取宿主令牌文件（bootstrap 是否已运行？）"
  KNOWWORK_HOST_TOKEN="$(cat "$TOKEN_FILE")"
fi
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
# 扩展的镜像多挂的配置层（例如在线编辑的插件）：KNOWWORK_HOST_BUNDLES 是逗号分隔的包名，包装在 $APP_DIR/node_modules 下，
# 链接进配置档、排在开源那几层之后（同 scripts/dev_stack.py）
extra="$(printf '%s' "${KNOWWORK_HOST_BUNDLES:-}" | tr ',' ' ')"
for name in $extra; do
  case "$name" in
    *[!@a-z0-9._/-]*|*..*) fail "配置层的包名不对：$name（KNOWWORK_HOST_BUNDLES）" ;;
  esac
  [ -d "$APP_DIR/node_modules/$name" ] || fail "找不到配置层 $name（KNOWWORK_HOST_BUNDLES），镜像里没装"
  mkdir -p "$staging/node_modules/$(dirname "$name")"
  ln -s "$APP_DIR/node_modules/$name" "$staging/node_modules/$name"
done
if [ -n "$extra" ]; then
  # shellcheck disable=SC2086  # 按空格拆成一个个包名
  node -e '
    const fs = require("fs")
    const [file, ...names] = process.argv.slice(1)
    const manifest = JSON.parse(fs.readFileSync(file, "utf8"))
    manifest.dsh.profile.bundles.push(...names)
    fs.writeFileSync(file, JSON.stringify(manifest, null, 2))
  ' "$staging/package.json" $extra
fi
# 上传的插件（只有个人专属宿主有）：业务服务在容器启动前把解开的插件拷进 KNOWWORK_HOST_PLUGIN_DIR，一个子目录一个。
# 按包里 package.json 的名字链接进配置档、加进插件包清单，排在最后；和已有的配置层同名的不装
if [ -n "${KNOWWORK_HOST_PLUGIN_DIR:-}" ] && [ -d "$KNOWWORK_HOST_PLUGIN_DIR" ]; then
  node -e '
    const fs = require("fs"), path = require("path")
    const [root, staging] = process.argv.slice(1)
    const file = path.join(staging, "package.json")
    const manifest = JSON.parse(fs.readFileSync(file, "utf8"))
    for (const entry of fs.readdirSync(root).sort()) {
      const dir = path.join(root, entry)
      let name
      try { name = JSON.parse(fs.readFileSync(path.join(dir, "package.json"), "utf8")).name } catch { continue }
      if (typeof name !== "string" || !/^(?:@[a-z0-9][a-z0-9._-]*\/)?[a-z0-9][a-z0-9._-]*$/.test(name)) continue
      const link = path.join(staging, "node_modules", name)
      if (fs.existsSync(link) || /^@(knowwork|deepseek-ai)\//.test(name)) continue
      fs.mkdirSync(path.dirname(link), { recursive: true })
      fs.symlinkSync(dir, link)
      manifest.dsh.profile.bundles.push(name)
    }
    fs.writeFileSync(file, JSON.stringify(manifest, null, 2))
  ' "$KNOWWORK_HOST_PLUGIN_DIR" "$staging"
fi
rm -rf "$profile"
mv "$staging" "$profile"

cd "$APP_DIR"
exec "$APP_DIR/node_modules/.bin/dsh" --profile "$PROFILE_NAME" --patch "$DEPLOY_PATCH" "$@"
