#!/bin/sh
# 写 GenOffice 的设置（Office 容器入口调用）：打开自带的 MCP 服务、关掉内置 AI 侧栏、修订与批注作者用这个人的名字。
#   settings.sh <设置文件> <作者名，可空> <MCP 端口> <GenOffice 可执行文件>
# 新容器（常见）没有设置文件：直接用 shell 写，不为一个 JSON 文件起一次 Electron（冷启动要多 0.5～1 秒）。
# 停了又拉起的容器里 GenOffice 用着时可能写过别的设置：用它自带的 Node 合并进去，名字里有什么字符都不会写坏 JSON。
set -e
file=$1
author=$2
port=$3
genoffice=$4
case $port in
  '' | *[!0-9]*)
    echo "GenOffice 的 MCP 端口不是数字：$port" >&2
    exit 1
    ;;
esac
mkdir -p "$(dirname "$file")"
if [ -e "$file" ]; then
  exec env ELECTRON_RUN_AS_NODE=1 "$genoffice" -e '
    const fs = require("fs")
    const [file, author, port] = process.argv.slice(1)
    let s = {}
    try { s = JSON.parse(fs.readFileSync(file, "utf8")) } catch {}
    s.mcpEnabled = true
    s.mcpPort = Number(port)
    s.aiPanelShowSidebar = false
    if (author) s.authorName = author
    fs.writeFileSync(file, JSON.stringify(s, null, 2))
  ' "$file" "$author" "$port"
fi
# JSON 字符串：去掉控制字符（名字里本不该有），再转义反斜杠和引号；其余（中文等）原样
name=$(printf '%s' "$author" | tr -d '\000-\037' | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')
if [ -n "$name" ]; then
  printf '{"mcpEnabled":true,"mcpPort":%s,"aiPanelShowSidebar":false,"authorName":"%s"}\n' "$port" "$name" > "$file"
else
  printf '{"mcpEnabled":true,"mcpPort":%s,"aiPanelShowSidebar":false}\n' "$port" > "$file"
fi
