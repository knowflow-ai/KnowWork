# 部署

## 环境要求

- Linux，x86_64（镜像只发布 linux/amd64）；
- Docker 24 以上，Docker Compose v2.24 以上；
- 能访问阿里云容器镜像服务（`crpi-6q638zy09wmnj70w.cn-hangzhou.personal.cr.aliyuncs.com`）。镜像里会话沙箱最大，约 2.2 GB。

## 一条命令安装

```sh
deploy/install.sh              # 默认：拉镜像、启动、等到就绪
deploy/install.sh --url http://公网IP:8080   # 指定浏览器访问的外部地址（云服务器的公网 IP 不在网卡上，要手动给）
deploy/install.sh --no-sandbox # 不启用会话沙箱
deploy/install.sh --no-pull    # 不拉镜像，用本机已有的（离线部署）
deploy/install.sh --timeout 600
```

脚本依次做这些事：

1. 检查 Docker 与 Compose 版本；
2. 第一次运行时从 `deploy/.env.example` 复制出 `deploy/.env`，把访问地址换成本机网卡上的 IP（给了 `--url` 就用它），
   生成对象存储的随机凭证；
3. 拉镜像，失败最多重试 3 次；
4. `docker compose up -d`，轮询 `/ready` 直到就绪，打印访问地址。

`deploy/.env` 里有凭证，权限是 600，不要提交到任何仓库。部署主密钥、宿主令牌、数据库口令由一次性容器
`bootstrap` 生成在 Docker 卷里，不在 `.env` 里。

就绪后浏览器打开打印出来的地址：按初始化向导创建管理员，再到「管理 → 模型」添加模型，模型别名填 `default`
（运行时宿主请求的就是这个名字）。

## 容器

| 容器 | 作用 |
|---|---|
| gateway | nginx：前端静态文件，反向代理 `/api/`，对外端口默认 8080 |
| api | 业务服务：公开接口、模型网关、工具桥、附件解析、审计；启动时自动执行数据库迁移 |
| worker | 后台任务：附件解析等；和 api 连同一个 Docker，把解析结果放进会话沙箱 |
| runtime-host | 运行时宿主：Node 与 DSH |
| postgres、redis、rustfs | 数据库、缓存与队列、对象存储 |
| bootstrap | 一次性：生成部署密钥后退出 |

会话沙箱容器由 api（以及放解析结果的 worker）按会话创建，空闲一段时间后自动回收，不在上表里。

## 访问地址与 HTTPS

`.env` 里两项要和实际访问方式一致：

- `KNOWWORK_PUBLIC_BASE_URL`：浏览器访问的外部地址，邀请链接与 OIDC 回调都用它拼。换域名或端口时同步改。
- `KNOWWORK_COOKIE_SECURE`：会话 Cookie 是否只走 HTTPS。默认 `false`，因为装好时是 http；
  如果设成 `true` 却用 http 访问，浏览器不会保存 Cookie，表现为登录后马上又回到登录页。

云服务器的公网 IP 通常不在网卡上，脚本自动取到的是内网地址，结束时会提醒。这时用
`deploy/install.sh --url http://公网IP:8080` 重跑即可，并在云厂商的安全组或防火墙里放行 8080 端口。

上 HTTPS 的做法：在网关前面放一层你自己的反向代理或负载均衡做 TLS 终止，转发到 8080；
然后把访问地址改成 `https://…`、`KNOWWORK_COOKIE_SECURE` 改成 `true`，执行 `docker compose up -d` 生效。

## 会话沙箱

默认打开。`deploy/.env` 里的 `COMPOSE_FILE` 带上了 `docker-compose.sandbox.yml`，它把本机的 `docker.sock`
挂给 api，api 按会话创建沙箱容器。

- **风险**：能创建容器就等于能控制这台机器的 Docker，挂 `docker.sock` 约等于给了 api 宿主机 root。只用于私有化单机部署。
  沙箱容器本身去掉了全部 Linux 能力、禁止提权，按会话隔离。
- **关闭**：`install.sh --no-sandbox`，或把 `COMPOSE_FILE` 改成只有 `docker-compose.yml`。关闭后模型不能执行命令，
  也生成不了 Word、Excel 这类文件。
- **远端 Docker**：去掉 `docker-compose.sandbox.yml`，在 `.env` 里打开 `KNOWWORK_SANDBOX_DOCKER_ENABLED`，
  填 `KNOWWORK_SANDBOX_DOCKER_HOST=tcp://主机:2376` 和 TLS 证书目录。
- **出网**：`KNOWWORK_SANDBOX_ALLOW_INTERNET=false` 时沙箱完全断网。

## 附件解析

PDF、Office 等附件的解析用 MinerU。部署后在「管理后台 → 模型 → 文档解析」里填 MinerU 地址，保存后几秒内生效，不用重启；也可以在 `.env` 里用 `KNOWWORK_MINERU_BASE_URL` 给个默认值，后台配了以后台为准。两边都没配时，上传这类附件会明确提示未配置。

## 升级

1. 先备份数据（见下一节）；
2. 换上新版本的部署文件：在仓库目录执行 `git fetch --tags && git checkout v0.0.3`（换成要升级到的版本）。编排文件
   可能随版本变，只改版本号会用旧的编排文件跑新镜像。`deploy/.env` 不在仓库里，切换版本不会动它；
3. 把 `deploy/.env` 的 `KNOWWORK_VERSION` 改成新版本；
4. 重新执行 `deploy/install.sh`：它会拉新镜像（包括会话沙箱镜像）并重启服务。

数据库迁移在 api 启动时自动执行（带锁，多实例不会重复跑）。每个版本要注意的事写在 [CHANGELOG](../CHANGELOG.md) 里。

## 备份

备份与恢复脚本还在做。目前请在停机后备份这些 Docker 卷（卷名前缀由 `KNOWWORK_VOLUME_PREFIX` 决定，默认 `knowwork`）：

| 卷 | 内容 |
|---|---|
| `knowwork_pg-data` | 数据库 |
| `knowwork_rustfs-data` | 附件、产物等文件 |
| `knowwork_secrets`、`knowwork_host-secrets`、`knowwork_db-secrets` | 部署主密钥与由它派生的凭证；主密钥丢了，库里加密保存的模型密钥就解不开了 |
| `knowwork_dsh-data` | 运行时宿主的会话数据 |

## 离线部署

在能联网的机器上 `docker pull` 全部镜像（`docker compose config --images` 列得出来）并 `docker save`，
拷到目标机器 `docker load`，再执行 `deploy/install.sh --no-pull`，并在 `.env` 里设 `KNOWWORK_PULL_POLICY=never`。

## 排查

- 看服务状态：在 `deploy/` 下 `docker compose ps`；
- 看日志：`docker compose logs api runtime-host`；
- 就绪检查：`curl http://127.0.0.1:8080/ready`，返回里写着数据库和宿主各自是否正常。
