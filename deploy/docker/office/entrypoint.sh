#!/bin/sh
# Office 容器入口（业务服务按人起）：写好 GenOffice 的设置（settings.sh），有口令时起 MCP 门卫，再在虚拟显示里起
# GenOffice 网页服务。设置与起法和桌面端的编辑器子进程一致：打开自带的
# MCP 服务、关掉内置 AI 侧栏、修订与批注作者用这个人的名字，`--web-only` 只做网页服务（GenOffice 补丁 2）。
#   KNOWWORK_OFFICE_AUTHOR      人的修订与批注作者名（AI 的作者名由每次调用带上）
#   KNOWWORK_OFFICE_MCP_TOKEN   MCP 门卫的口令；不设就不开门卫
#   KNOWWORK_OFFICE_MCP_PORT    GenOffice 自带 MCP 服务的端口（容器内 127.0.0.1，门卫转给它），默认 3093
set -e
GO=/opt/genoffice/genoffice
/opt/knowwork/settings.sh "$HOME/.config/GenOffice/app-settings.json" "${KNOWWORK_OFFICE_AUTHOR:-}" \
  "${KNOWWORK_OFFICE_MCP_PORT:-3093}" "$GO"
if [ -n "${KNOWWORK_OFFICE_MCP_TOKEN:-}" ]; then
  ELECTRON_RUN_AS_NODE=1 "$GO" /opt/knowwork/mcp-gate.mjs &
fi
# 虚拟显示还是要：转 PDF、打印走 GenOffice 的隐藏窗口，没有显示服务起不来
exec xvfb-run -a -s "-screen 0 1280x800x24" "$GO" --no-sandbox --web-only "$@"
