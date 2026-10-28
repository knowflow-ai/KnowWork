#!/bin/sh
# KnowWork 一条命令启动：检查 Docker 环境 → 准备 .env → 拉镜像（失败最多重试 3 次）→ 启动 → 轮询 /ready。
#
# 用法：deploy/install.sh [--url 地址] [--no-pull] [--no-sandbox] [--timeout 秒]
#   --url 地址    浏览器访问的外部地址，例如 http://公网IP:8080 或 https://域名（默认取本机网卡上的 IP）
#   --no-pull     跳过拉取（离线部署或使用本地构建的镜像）
#   --no-sandbox  不启用会话沙箱（不挂 docker.sock；模型不能执行命令，也生成不了 Word、Excel）
#   --timeout N   等待就绪的最长秒数（默认取 .env 的 KNOWWORK_READY_TIMEOUT，否则 300）
set -eu

MIN_DOCKER_VERSION="24.0.0"
MIN_COMPOSE_VERSION="2.24.0"
PULL_ATTEMPTS=3
# 两次拉取之间的基础等待秒数（第 n 次失败后等 n 倍）
PULL_RETRY_DELAY="${KNOWWORK_PULL_RETRY_DELAY:-5}"

SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
SANDBOX_FILE="docker-compose.sandbox.yml"
ENV_EXAMPLE="$SCRIPT_DIR/.env.example"

log() { printf '[knowwork] %s\n' "$*"; }
die() { printf '[knowwork] 错误：%s\n' "$*" >&2; exit 1; }

usage() {
  sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'
}

# version_ge A B：A >= B（只比较数字段，忽略预发布后缀）
version_ge() {
  awk -v a="$1" -v b="$2" 'BEGIN {
    n = split(a, x, "."); m = split(b, y, ".")
    for (i = 1; i <= 3; i++) {
      xi = (i <= n) ? x[i] + 0 : 0; yi = (i <= m) ? y[i] + 0 : 0
      if (xi > yi) exit 0
      if (xi < yi) exit 1
    }
    exit 0
  }'
}

# 从 .env 读取一个键的值（不 source 文件，避免执行其中内容）
env_value() {
  [ -f "$ENV_FILE" ] || return 0
  sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1
}

random_hex() {
  od -An -N24 -tx1 /dev/urandom | tr -d ' \n'
}

check_docker() {
  command -v docker >/dev/null 2>&1 || die "未找到 docker，请先安装 Docker ${MIN_DOCKER_VERSION} 以上版本"
  docker_version="$(docker version --format '{{.Server.Version}}' 2>/dev/null)" \
    || die "无法连接 Docker 守护进程（是否已启动、当前用户是否有权限？）"
  version_ge "$docker_version" "$MIN_DOCKER_VERSION" \
    || die "Docker 版本 ${docker_version} 过低，需要 ${MIN_DOCKER_VERSION} 以上"
  compose_version="$(docker compose version --short 2>/dev/null)" \
    || die "未找到 docker compose 插件（需要 Compose v2 ${MIN_COMPOSE_VERSION} 以上）"
  compose_version="${compose_version#v}"
  version_ge "$compose_version" "$MIN_COMPOSE_VERSION" \
    || die "Docker Compose 版本 ${compose_version} 过低，需要 ${MIN_COMPOSE_VERSION} 以上"
  log "Docker ${docker_version}，Compose ${compose_version}"
}

# 设置 .env 里一个键的值（只改这一行，其余原样保留；没有这个键就追加）。值里不能有 | 与换行
set_env() {
  key="$1"; value="$2"
  tmp="$ENV_FILE.tmp.$$"
  if grep -q "^$key=" "$ENV_FILE"; then
    sed "s|^$key=.*|$key=$value|" "$ENV_FILE" > "$tmp"
  else
    cat "$ENV_FILE" > "$tmp"
    printf '%s=%s\n' "$key" "$value" >> "$tmp"
  fi
  mv "$tmp" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
}

# 把 .env 中值为空的键填上随机值
fill_secret() {
  [ -n "$(env_value "$1")" ] && return 0
  set_env "$1" "$(random_hex)"
  log "已生成 $1"
}

# 本机对外的 IP：取默认路由的源地址，取不到用 hostname -I 的第一个，再不行用 localhost
host_address() {
  address="$(ip route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") { print $(i + 1); exit }}')"
  [ -n "$address" ] || address="$(hostname -I 2>/dev/null | awk '{print $1}')"
  printf '%s' "${address:-localhost}"
}

prepare_env() {
  if [ ! -f "$ENV_FILE" ]; then
    [ -f "$ENV_EXAMPLE" ] || die "缺少 $ENV_EXAMPLE"
    (umask 077 && cp "$ENV_EXAMPLE" "$ENV_FILE")
    port="$(env_value KNOWWORK_PORT)"
    set_env KNOWWORK_PUBLIC_BASE_URL "http://$(host_address):${port:-8080}"
    log "已从 .env.example 创建 .env，访问地址设为 $(env_value KNOWWORK_PUBLIC_BASE_URL)（换域名或端口时改这一项）"
  fi
  fill_secret KNOWWORK_RUSTFS_ACCESS_KEY
  fill_secret KNOWWORK_RUSTFS_SECRET_KEY
  if [ -n "$public_url" ]; then
    set_env KNOWWORK_PUBLIC_BASE_URL "${public_url%/}"
    log "访问地址设为 ${public_url%/}"
  fi
  if [ "$no_sandbox" -eq 1 ]; then
    # 只去掉沙箱那一份：整个重置成 docker-compose.yml 会冲掉用户自己加的覆盖文件和扩展的叠加文件
    files="$(compose_files | grep -vx "$SANDBOX_FILE" | paste -s -d: -)"
    set_env COMPOSE_FILE "${files:-docker-compose.yml}"
    log "不启用会话沙箱：模型不能执行命令，也生成不了 Word、Excel"
  fi
  case "$(env_value KNOWWORK_PUBLIC_BASE_URL)" in
    http://*) [ "$(env_value KNOWWORK_COOKIE_SECURE)" = "true" ] \
      && log "提醒：访问地址是 http，但 KNOWWORK_COOKIE_SECURE=true，浏览器不会保存登录 Cookie" ;;
  esac
  return 0
}

# .env 里 COMPOSE_FILE 列出的编排文件（冒号分隔，相对 deploy/），转成 -f 参数
compose_files() {
  files="$(env_value COMPOSE_FILE)"
  [ -n "$files" ] || files="docker-compose.yml"
  printf '%s\n' "$files" | tr ':' '\n'
}

sandbox_enabled() {
  compose_files | grep -qx "$SANDBOX_FILE"
}

# sh 没有数组：把文件倒序逐个插到参数最前面，最终顺序与 COMPOSE_FILE 一致
compose() {
  set -- --project-directory "$SCRIPT_DIR" --env-file "$ENV_FILE" "$@"
  for file in $(compose_files | sed '1!G;h;$!d'); do
    set -- -f "$SCRIPT_DIR/$file" "$@"
  done
  docker compose "$@"
}

pull_images() {
  attempt=1
  while :; do
    log "拉取镜像（第 ${attempt}/${PULL_ATTEMPTS} 次）"
    if compose pull --quiet && { ! sandbox_enabled || compose --profile sandbox pull --quiet sandbox-image; }; then
      return 0
    fi
    if [ "$attempt" -ge "$PULL_ATTEMPTS" ]; then
      # 私有镜像仓库没登录时 pull 也只报拉取失败，提示里带上登录命令（docker login 只要主机，不要命名空间）
      registry="$(env_value KNOWWORK_REGISTRY)"
      hint=""
      [ -z "$registry" ] || hint="；镜像仓库需要登录的，先执行 docker login ${registry%%/*}"
      die "镜像拉取连续 ${PULL_ATTEMPTS} 次失败，请检查网络或镜像仓库地址（KNOWWORK_REGISTRY）${hint}"
    fi
    sleep $((attempt * PULL_RETRY_DELAY))
    attempt=$((attempt + 1))
  done
}

http_status() {
  if command -v curl >/dev/null 2>&1; then
    curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$1" 2>/dev/null || printf '000'
  elif command -v wget >/dev/null 2>&1; then
    if wget -q -O /dev/null -T 5 "$1" 2>/dev/null; then printf '200'; else printf '000'; fi
  else
    die "需要 curl 或 wget 来检查服务是否就绪"
  fi
}

wait_ready() {
  port="$(env_value KNOWWORK_PORT)"
  url="http://127.0.0.1:${port:-8080}/ready"
  deadline=$(( $(date +%s) + timeout ))
  # 紧挨中文的变量要写成 ${url}：macOS 自带的 bash 3.2 在 UTF-8 下会把后面的全角字符当成变量名的一部分
  log "等待服务就绪：${url}（最多 ${timeout} 秒）"
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if [ "$(http_status "$url")" = "200" ]; then
      log "KnowWork 已就绪。浏览器打开 $(env_value KNOWWORK_PUBLIC_BASE_URL) ，按初始化向导创建管理员，再到「管理 → 模型」添加模型。"
      # 云服务器的公网 IP 通常不在网卡上，自动取到的是内网地址：邀请链接、单点登录回调会指向它
      case "$(env_value KNOWWORK_PUBLIC_BASE_URL)" in
        http://10.*|http://192.168.*|http://172.1[6-9].*|http://172.2[0-9].*|http://172.3[01].*)
          log "提醒：这是内网地址。从公网访问时用 --url http://公网IP:${port:-8080} 重跑本脚本（或改 .env 的 KNOWWORK_PUBLIC_BASE_URL），云服务器还要在安全组放行 ${port:-8080} 端口" ;;
      esac
      return 0
    fi
    sleep 3
  done
  compose ps >&2 || true
  die "等待 ${timeout} 秒仍未就绪。排查：cd $SCRIPT_DIR && docker compose logs api runtime-host"
}

main() {
  skip_pull=0
  no_sandbox=0
  public_url=""
  timeout=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --no-pull) skip_pull=1 ;;
      --no-sandbox) no_sandbox=1 ;;
      --url)
        [ $# -ge 2 ] || die "--url 需要一个地址，例如 http://公网IP:8080"
        case "$2" in http://*|https://*) public_url="$2" ;; *) die "--url 要以 http:// 或 https:// 开头" ;; esac
        shift ;;
      --timeout)
        [ $# -ge 2 ] || die "--timeout 需要一个秒数"
        timeout="$2"; shift ;;
      -h|--help) usage; exit 0 ;;
      *) die "未知参数：$1（--help 查看用法）" ;;
    esac
    shift
  done

  check_docker
  prepare_env
  [ -n "$timeout" ] || timeout="$(env_value KNOWWORK_READY_TIMEOUT)"
  timeout="${timeout:-300}"
  case "$timeout" in ''|*[!0-9]*) die "等待时长必须是正整数秒" ;; esac

  compose config --quiet || die "编排配置校验失败"
  [ "$skip_pull" -eq 1 ] || pull_images
  log "启动服务"
  compose up -d --remove-orphans
  wait_ready
}

main "$@"
