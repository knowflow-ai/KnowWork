# 配置

部署配置都在 `deploy/.env`，第一次运行 `deploy/install.sh` 时从 `deploy/.env.example` 生成。
每一项在 `.env.example` 里都有注释，留空即用默认值。这里只列常改的几项。改完在 `deploy/` 下执行
`docker compose up -d` 生效。

模型、知识库、工具、成员与权限、品牌都在网页的管理后台里配，不在 `.env` 里。

## 访问

| 配置项 | 默认 | 说明 |
|---|---|---|
| `KNOWWORK_PORT` | `8080` | 网关对外端口 |
| `KNOWWORK_BIND_ADDRESS` | `0.0.0.0` | 只想本机访问时改成 `127.0.0.1` |
| `KNOWWORK_PUBLIC_BASE_URL` | 本机 IP 加端口 | 浏览器访问的外部地址，邀请链接与 OIDC 回调据此拼出 |
| `KNOWWORK_COOKIE_SECURE` | `false` | 配好 HTTPS 后改成 `true`；用 http 访问时必须是 `false`，否则登录不了 |

## 单点登录（OIDC）

`KNOWWORK_OIDC_ISSUER` 留空即不启用。启用时同时填 `KNOWWORK_OIDC_CLIENT_ID`、`KNOWWORK_OIDC_CLIENT_SECRET`、
`KNOWWORK_OIDC_REDIRECT_URI`，回调地址是 `<访问地址>/api/auth/oidc/callback`。

## 模型

模型在「管理 → 模型」里添加：接 OpenAI 兼容接口的厂商（DeepSeek、通义千问、自建 vLLM 等），密钥加密存在库里，
只在服务端使用。运行时宿主请求的模型别名是 `default`（`KNOWWORK_HOST_MODEL_ALIAS`），至少要有一个模型用这个别名。

对话模型可以设「思考」：跟随服务默认（不传参数）、开启或关闭。Qwen3 这类模型在自建服务上默认开着思考，每一步先想一段再动手，
回答要多等几秒，关掉会快不少。各家关思考的写法不同，按接口地址认：阿里百炼、硅基流动等用 `enable_thinking`，DeepSeek、
智谱、火山方舟等用 `thinking.type`，认不出的按自建的 vLLM、SGLang 处理（`chat_template_kwargs.enable_thinking`）；OpenAI
关不掉推理，不传。

`.env` 里的 `KNOWWORK_MODEL_UPSTREAM_*` 是可选的固定上游兜底，留空不用。

## 会话沙箱

| 配置项 | 默认 | 说明 |
|---|---|---|
| `COMPOSE_FILE` | 带上 `docker-compose.sandbox.yml` | 本机沙箱的开关，去掉第二个文件即关闭 |
| `KNOWWORK_SANDBOX_ALLOW_INTERNET` | `true` | `false` 时沙箱完全断网 |
| `KNOWWORK_SANDBOX_RUNTIME` | 空 | 可选的 OCI runtime，例如已安装的 `runsc`（gVisor） |
| `KNOWWORK_SANDBOX_DOCKER_HOST` | 空 | 远端 Docker，见[部署](deploy.md#会话沙箱) |

## 外部服务

| 配置项 | 说明 |
|---|---|
| `KNOWWORK_MINERU_BASE_URL` | MinerU 文档解析服务地址的默认值。更常用的做法是在「管理后台 → 模型 → 文档解析」里配，那里配了以那里为准、保存后几秒内生效，不用重启；两边都没配时 PDF、Word、PPT 不解析 |
| `KNOWWORK_MINERU_SERVER_URL` | MinerU 的 VLM 推理服务地址（sglang 或 vllm，常见 30000 端口）的默认值，界面里也能配。这是 MinerU 去访问的地址，和 MinerU 装在同一台机器时写 `http://127.0.0.1:30000`；解析模式为「自动」时，配了知识库用 hybrid 模式，不配用 pipeline 模式。界面里还能把解析模式强制成 VLM（`vlm-http-client`）、hybrid 或 pipeline，前两种必须配这个地址 |
| `KNOWWORK_MINERU_MAX_CONCURRENCY` | MinerU 同时最多接几个解析（默认 `3`），按 MinerU 的 `max_concurrent_requests` 设；所有 worker 进程共用这个名额。界面里配了时用界面里的「同时解析上限」 |

## 后台任务

| 配置项 | 默认 | 说明 |
|---|---|---|
| `KNOWWORK_JOB_CORE_SLOTS` | `2` | worker 同时处理几个解析任务；解析服务本身的并发上限更小时，加大它没用 |
| `KNOWWORK_JOB_GRACE_SECONDS` | `60` | worker 停机时等手上任务退回排队的时间，过了直接中止，下次启动重新处理 |
| `KNOWWORK_REDIS_URL` | 部署里已配 | 下游并发闸门用：多个 worker 进程共用 MinerU 等下游的名额；为空或 Redis 不可用时闸门只在单个进程内生效 |

## 镜像

| 配置项 | 默认 | 说明 |
|---|---|---|
| `KNOWWORK_REGISTRY` | `crpi-6q638zy09wmnj70w.cn-hangzhou.personal.cr.aliyuncs.com/knowflow-ai` | 镜像仓库地址加命名空间；postgres、redis、rustfs 也从这里拉 |
| `KNOWWORK_VERSION` | 当前版本 | 升级时改它 |
| `KNOWWORK_PULL_POLICY` | `missing` | `never` 用于离线部署 |

## 网络

api 与运行时宿主之间有一个内部网段（默认 `172.30.240.0/24`）。和宿主机已有网段冲突时，改
`KNOWWORK_HOST_LINK_SUBNET` 与 `KNOWWORK_API_HOST_LINK_IP`，IP 必须在网段内。
