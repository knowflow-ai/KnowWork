# syntax=docker/dockerfile:1.7
# 会话沙箱镜像（照 WeKnora 的 docker/Dockerfile.sandbox 的 runtime 目标）：Python 3.12、Node.js 22、Bash、jq、/workspace。
# Office 工具链照 KnowFlow 的沙箱镜像（docker/sandbox/Dockerfile）：原生 LibreOffice（无界面的 Writer、Calc、Impress）
# 负责转 PDF、渲染、重算公式与文档预览，poppler 出页面图片，pandoc / markitdown 读文档，docx-js 与 pptxgenjs 生成
# Word 与 PPT；KnowFlow 自己写的辅助脚本放 /opt/knowwork/office，Office 技能按这个绝对路径引用。
# 不用 DSH 官方的 LibreOffice Kit：它在 Linux 上只有 WASM 引擎，实测同一份文档每次约 17 秒、内存峰值约 1.4 GB；
# 原生引擎 2–5 秒、约 190 MB（2026-10-20）。
# 一会话一个长驻容器，api 用它按会话创建。
# 构建上下文为仓库根目录：docker build -f deploy/docker/sandbox.Dockerfile -t ghcr.io/knowwork/knowwork-sandbox:dev .
# 或 docker compose -f deploy/docker-compose.yml --profile sandbox build sandbox-image
#
# 与 WeKnora 的差异：不带 Cube 的 envd 与图形桌面目标（我们只用 Docker 后端）；不预装 git
# （WeKnora 用它做工作区检查点，我们没有这项）。
# Debian trixie（与 KnowFlow 沙箱镜像的 python:3.12-slim 一致）：LibreOffice 25.2。bookworm 只有 7.4，
# 而照搬来的 Office 技能是在 25.2 上验证的（新函数、渲染与重算行为都有差异）
ARG PYTHON_IMAGE=python:3.12-slim-trixie
ARG NODE_IMAGE=node:22-bookworm-slim
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.7.8
ARG NPM_REGISTRY=https://registry.npmjs.org

# FROM 都经 ARG 指定镜像，版本钉在 ARG 默认值里；hadolint 看不穿 ARG，按行忽略 DL3006。
# hadolint ignore=DL3006
FROM ${NODE_IMAGE} AS node-base

# hadolint ignore=DL3006
FROM ${UV_IMAGE} AS uv-base

# hadolint ignore=DL3006
FROM ${PYTHON_IMAGE} AS sandbox

# 3.12 是下限：技能源码常用 PEP 701 的 f-string 嵌套引号，3.11 解析不了（WeKnora 同此理由）。

# Node.js：直接从官方镜像拷二进制，省掉 NodeSource 的安装开销
COPY --from=node-base /usr/local/bin/node /usr/local/bin/
COPY --from=node-base /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

# Python 包管理器 uv（比 pip 快得多，技能装依赖时用）
COPY --from=uv-base /uv /usr/local/bin/uv

# 命令行工具：bash、coreutils（timeout、stat、head、tail）、findutils、util-linux（setsid）slim 已自带；
# file、jq、curl、压缩工具与 WeKnora 相同；procps 提供 ps，排查进程用。
# 字体：中文给 matplotlib 出图与 Office 转换用；Carlito、Caladea、Liberation 与 Calibri、Cambria、Arial/Times
# 度量兼容，Office 文档转 PDF 时版式不跑。
# 不钉 apt 包版本：Debian 镜像站只留当前版本，钉死的旧版本被移走后构建就失败；基础镜像已钉发行版
# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends \
      jq curl ca-certificates file zip unzip bzip2 xz-utils zstd procps \
      fonts-noto-cjk fonts-crosextra-carlito fonts-crosextra-caladea fonts-liberation2 \
      libreoffice-writer-nogui libreoffice-calc-nogui libreoffice-impress-nogui poppler-utils pandoc \
 && rm -rf /var/lib/apt/lists/* /var/cache/apt/*

# 产物需要的 Python 包（版本照 KnowFlow 沙箱镜像）：表格、Word、PPT、图表，以及 Office 脚本用的 lxml、defusedxml，
# 读文档用的 markitdown。
RUN pip install --no-cache-dir \
      pandas==2.2.3 numpy==2.1.3 openpyxl==3.1.5 XlsxWriter==3.2.0 matplotlib==3.9.2 \
      python-docx==1.2.0 python-pptx==1.0.2 Pillow==11.0.0 tabulate==0.9.0 lxml==5.3.0 defusedxml==0.7.1 \
      "markitdown[docx,pptx,xlsx]==0.1.2" \
 && rm -rf /root/.cache

# 生成 Word 与 PPT 的 Node 库（版本照 KnowFlow）。装成全局包，NODE_PATH 让任何位置的脚本都能 require('docx')；
# react、react-icons、sharp 给 PPT 技能把图标渲染成图片用。
ARG NPM_REGISTRY
ENV NODE_PATH=/usr/local/lib/node_modules
RUN npm install -g --no-audit --no-fund --registry="${NPM_REGISTRY}" \
      docx@9.5.1 pptxgenjs@4.0.1 react@18.3.1 react-dom@18.3.1 react-icons@5.5.0 sharp@0.34.3 \
 && npm cache clean --force

# Office 辅助脚本（照搬 KnowFlow 的 docker/sandbox/office）：soffice 封装、公式重算、修订、批注、校验、缩略图等
COPY deploy/docker/sandbox-office /opt/knowwork/office

# 命令与文件操作默认以 root 运行：一个沙箱只属于一个会话，容器里的账号不是隔离边界，模型要装包、写到哪都行。
# 仍保留 uid 1000 的 user 账号，供显式按账号执行（与 WeKnora 相同）。
RUN groupadd -g 1000 user \
 && useradd -u 1000 -g user -m -s /bin/bash user

# 预建附件目录；属主给 user，显式以 user 执行时也能写，默认的 root 不依赖这一点
RUN mkdir -p /workspace/input \
 && chown -R user:user /workspace

ENV LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg
WORKDIR /workspace
# 有意以 root 运行，理由见上面「命令与文件操作默认以 root 运行」；容器 CapDrop ALL 加 no-new-privileges
# hadolint ignore=DL3002
USER root
