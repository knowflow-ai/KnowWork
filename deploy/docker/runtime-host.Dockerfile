# syntax=docker/dockerfile:1.7
# 运行时宿主镜像：Node 24 + DSH（所有 DSH 包锁在 0.2.1-rc.2，由 package-lock.json 固定）。
# 构建上下文为仓库根目录：docker build -f deploy/docker/runtime-host.Dockerfile .
ARG NODE_IMAGE=node:24.18.0-bookworm-slim

# FROM 都经 ARG 指定镜像，版本钉在 ARG 默认值里；hadolint 看不穿 ARG，按行忽略 DL3006。
# hadolint ignore=DL3006
FROM ${NODE_IMAGE} AS deps
WORKDIR /app
COPY runtime-host/package.json runtime-host/package-lock.json ./
# 只装生产依赖：官方模拟模型服务与回放工具是测试用的 devDependencies
RUN --mount=type=cache,target=/root/.npm \
    npm ci --omit=dev --no-audit --no-fund \
 && test "$(node -p "require('@deepseek-ai/dsh/package.json').version")" = "0.2.1-rc.2"

# hadolint ignore=DL3006
FROM ${NODE_IMAGE} AS runtime
ENV NODE_ENV=production \
    DSH_HOME=/data/dsh \
    KNOWWORK_APP_DIR=/app \
    KNOWWORK_HOST_PORT=7300 \
    KNOWWORK_CREDENTIALS_PATH=/run/knowwork-host-private/credentials.yaml
WORKDIR /app
# 代码归 root、只读；运行用户 node（uid 1000）只能写 DSH_HOME 与凭据 tmpfs
COPY --from=deps /app/node_modules ./node_modules
COPY runtime-host/package.json ./package.json
COPY runtime-host/profile ./profile
COPY runtime-host/plugins ./plugins
COPY deploy/runtime-host/deploy.patch.yml ./deploy.patch.yml
COPY --chmod=0755 deploy/runtime-host/entrypoint.sh /usr/local/bin/knowwork-host-entrypoint
RUN mkdir -p /data/dsh /run/knowwork-host-private \
 && chown node:node /data/dsh /run/knowwork-host-private
USER node
EXPOSE 7300
ENTRYPOINT ["/usr/local/bin/knowwork-host-entrypoint"]
