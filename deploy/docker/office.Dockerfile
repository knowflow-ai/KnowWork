# KnowWork Office 镜像（开源）：GenOffice 网页服务模式跑在容器里，无界面（xvfb）。一个镜像两种角色：业务服务按人起的网页编辑器
# （编辑集成由扩展提供）；常驻的转换服务（逐项替换会话沙箱里的 LibreOffice）。
# 一个文件从头编到尾：取 GenOffice 的固定版本（office/UPSTREAM）、打上我们的补丁（office/patches）→ 编 GenOffice → 运行镜像。
#   docker build -f deploy/docker/office.Dockerfile -t knowwork-office:dev .
# 编 GenOffice 要好几 GB 磁盘，Linux 上约 8 分钟；发布时按架构在原生机器上编（release.yml），输入没变就复用上一版。
ARG NODE_IMAGE=node:24.18.0-bookworm
ARG RUNTIME_IMAGE=debian:bookworm-slim

# ── 源码：只取固定的那个提交，按顺序打补丁（同 office/apply.sh，补丁集怎么维护见那里）────────────────
# FROM 经 ARG 指定镜像，版本钉在 ARG 默认值里；hadolint 看不穿 ARG，按行忽略 DL3006
# hadolint ignore=DL3006
FROM ${NODE_IMAGE} AS source
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
COPY deploy/docker/office/UPSTREAM /office/UPSTREAM
COPY deploy/docker/office/patches/ /office/patches/
WORKDIR /src
# 固定的提交在社区 PR 的分支上：先按提交号浅取，取不到再取整个 PR 分支（repository、ref、commit 来自 UPSTREAM）
# hadolint ignore=SC2154
RUN . /office/UPSTREAM \
    && git init -q . \
    && { git fetch -q --depth 1 "$repository" "$commit" || git fetch -q "$repository" "$ref"; } \
    && git checkout -q "$commit" \
    && git -c user.name=knowwork -c user.email=knowwork@localhost am -q --3way /office/patches/*.patch \
    && rm -rf .git

# ── 编 GenOffice：Rust 表格引擎、六个编辑器，用 electron-builder 打成解包目录 ─────────────────────
# hadolint ignore=DL3006
FROM ${NODE_IMAGE} AS build
ARG RUST_VERSION=stable
ENV CARGO_HOME=/opt/cargo RUSTUP_HOME=/opt/rustup PATH=/opt/cargo/bin:$PATH
# 管道里 curl 失败也要让这一步失败（默认的 sh 只看最后一个命令）
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN curl -fsSL https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain "${RUST_VERSION}"
COPY --from=source /src /src
WORKDIR /src
# postinstall 会下载对应架构的 Electron；playwright 只给它自己的端到端测试用，不下浏览器
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
# 构建机要经代理出网时（例如本机开发）：除了 --build-arg HTTPS_PROXY=…（apt、npm、curl、cargo 都认），Electron 的下载器
# 要另外打开代理：--build-arg ELECTRON_GET_USE_PROXY=1 --build-arg GLOBAL_AGENT_HTTPS_PROXY=<同一个代理>。不传就不走代理
ARG ELECTRON_GET_USE_PROXY
ARG GLOBAL_AGENT_HTTPS_PROXY
RUN --mount=type=cache,target=/root/.npm --mount=type=cache,target=/root/.cache/electron \
    npm ci --no-audit --no-fund
RUN --mount=type=cache,target=/opt/cargo/registry npm run notices && npm run build:all
# arm64 上要打 arm64 包（配置里默认 x64），表格引擎用本机编出来的 target/release
WORKDIR /src/apps/shell
RUN if [ "$(uname -m)" = "aarch64" ]; then export GENOFFICE_LINUX_ARM64=1; fi; \
    npx electron-builder --linux dir --publish never \
    && mv release/linux-*unpacked /opt/genoffice

# ── 运行镜像：只带编好的 GenOffice、Electron 的运行库、xvfb 与字体 ─────────────────────────────────
# hadolint ignore=DL3006
FROM ${RUNTIME_IMAGE} AS runtime
# Electron 的运行库 + 虚拟显示（打印成 PDF、表格导出走隐藏窗口，没有显示服务起不来）+ 字体。
# 字体与会话沙箱镜像同一套（中文 Noto CJK，Calibri/Cambria 的同度量替身 Carlito/Caladea，Arial/Times 的 Liberation），
# 编辑、预览、导出用同一套字体，版式才一致。系统包跟着底座的版本走，不逐个钉版本
# 网络不稳时下载会断（中文字体包就有六十多 MB）：每个包重试几次
# hadolint ignore=DL3008
RUN apt-get -o Acquire::Retries=5 update && apt-get -o Acquire::Retries=5 install -y --no-install-recommends \
      ca-certificates tini xvfb xauth \
      libgtk-3-0 libnss3 libxss1 libasound2 libgbm1 libxshmfence1 libdrm2 libxkbcommon0 libsecret-1-0 \
      fonts-noto-cjk fonts-crosextra-carlito fonts-crosextra-caladea fonts-liberation2 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /opt/genoffice /opt/genoffice
COPY deploy/docker/office/entrypoint.sh deploy/docker/office/settings.sh deploy/docker/office/mcp-gate.mjs /opt/knowwork/
RUN useradd -m -u 1000 office && mkdir -p /work && chown office /work
# 账号 office（uid 1000）：业务服务往容器里放文件、在里面算摘要都用它
USER 1000
WORKDIR /work
# 8931 网页编辑器；8932 MCP 门卫（智能体改文档，要口令 KNOWWORK_OFFICE_MCP_TOKEN）
EXPOSE 8931 8932
ARG KNOWWORK_VERSION=dev
# 界面语言跟 LANG 走（Electron 在 Linux 上按它取语言）。
# 容器本身就是隔离边界（去掉全部能力后 Chromium 的沙箱起不来），所以 --no-sandbox；
# 端口只在容器网络里，口令由外面注入（GENOFFICE_WEB_TOKEN、KNOWWORK_OFFICE_MCP_TOKEN）
ENV LANG=zh_CN.UTF-8 LANGUAGE=zh_CN \
    GENOFFICE_WEB=1 GENOFFICE_WEB_HOST=0.0.0.0 GENOFFICE_WEB_PORT=8931 \
    KNOWWORK_VERSION=${KNOWWORK_VERSION}
# tini 当 1 号进程：xvfb-run 靠 USR1 信号等虚拟显示就绪，自己当 1 号进程时等不到；也替门卫、GenOffice 收僵尸进程
ENTRYPOINT ["/usr/bin/tini", "--", "/opt/knowwork/entrypoint.sh"]
