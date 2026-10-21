# syntax=docker/dockerfile:1.7
# 业务服务镜像：api、worker、bootstrap 三个容器共用，启动命令不同。
# 构建上下文为仓库根目录：docker build -f deploy/docker/server.Dockerfile .
ARG PYTHON_IMAGE=python:3.12.12-slim-bookworm
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.7.8

# FROM 都经 ARG 指定镜像，版本钉在 ARG 默认值里；hadolint 看不穿 ARG，按行忽略 DL3006。
# hadolint ignore=DL3006
FROM ${UV_IMAGE} AS uv

# hadolint ignore=DL3006
FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
# 先只装依赖（锁文件不变时这一层可复用），再装项目本身
COPY server/pyproject.toml server/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY server/src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# hadolint ignore=DL3006
FROM ${PYTHON_IMAGE} AS runtime
ARG APP_UID=10001
RUN groupadd --system --gid "${APP_UID}" knowwork \
 && useradd --system --uid "${APP_UID}" --gid knowwork --home-dir /nonexistent --no-create-home \
    --shell /usr/sbin/nologin knowwork
COPY --from=build /opt/venv /opt/venv
# 版本由发布流程传入（与镜像标签、仓库根的 VERSION 一致），系统设置页显示它；本地构建不传就是 dev
ARG KNOWWORK_VERSION=dev
ENV KNOWWORK_VERSION=${KNOWWORK_VERSION} \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
USER ${APP_UID}
EXPOSE 8000 8001
# 生产入口：一个进程起公开端口与内部端口
CMD ["python", "-m", "knowwork_server"]
