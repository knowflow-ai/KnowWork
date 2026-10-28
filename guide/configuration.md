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
| `KNOWWORK_PUBLIC_BASE_URL` | 本机 IP 加端口 | 浏览器访问的外部地址，邀请链接、OIDC 回调都据此拼出 |
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

## IM 机器人：企业微信、飞书、钉钉

员工可以在企业微信、飞书、钉钉里直接向智能助手提问，不用登录 KnowWork。机器人不认人：私聊、群里都只查给它选的
知识库，谁能找到机器人、拉它进群，由 IM 平台的可见范围决定。要按个人权限查资料，请到 KnowWork 网页。
三家都走长连接：业务服务主动连出去，不需要公网回调地址，也不用开入站端口，只要业务服务能访问外网。

在「管理 → 渠道」里添加机器人，选平台，填下面拿到的凭据。保存后状态变成「已连接」就能用；连不上时状态栏会写原因。

| 平台 | 在哪里建 | 填什么 |
|---|---|---|
| 企业微信 | 企业微信里新建「智能机器人」，接入方式选 API 模式、连接方式选「使用长连接」，设好可见范围 | 页面上的 Bot ID 与 Secret（点「点击获取」）。注意不是自建应用的 AgentId（1000002 这种纯数字）与应用 Secret；机器人名称填群里 @ 它时的名字 |
| 飞书 | 飞书开放平台建企业自建应用，开启机器人能力；事件订阅选「使用长连接接收事件」并订阅「接收消息」；开通读取单聊消息、读取群里 @ 机器人的消息、发送消息与卡片（CardKit）的权限；发布版本 | 应用的 App ID 与 App Secret |
| 钉钉 | 钉钉开放平台建企业内部应用，添加机器人能力，消息接收模式选 Stream 模式；开通互动卡片的写入与流式更新权限，要发回答里的图再开通「企业内机器人发送消息」权限；发布 | 应用的 Client ID（AppKey）与 Client Secret（AppSecret） |

- 每个机器人至少选一个知识库。能找到机器人的人都能查到这些知识库，只放大家都可以看的。
- 保存时会建一个和机器人同名的「渠道账号」，选的知识库同步成它的使用权限，在「管理 → 资源授权」里看得到；
  问答记在它名下，在「管理 → 运行记录」里按它筛。删掉机器人时这些权限一并收回。
- 群里只有 @ 机器人的消息才会被处理。每个人私聊、在每个群里各有一个对话，「渠道 → 谁在问」里能看到谁问过。
- 回答一边生成一边更新（企业微信是流式消息，飞书、钉钉是流式卡片）。钉钉卡片发不出去（多半是没开卡片权限）时
  改成答完回一条消息。
- 发「新会话」另起一个对话。需要人工确认或追问补充信息的操作，机器人这里做不了，会停下说明。
- 回答里引用了知识库的图时，答完会把图发出来，排在回答后面（最多 10 张）。图由业务服务直接传到平台上，不需要
  员工手机能打开 KnowWork 的地址。企业微信、钉钉每张图单独发一条消息（企业微信只发 jpg、png），飞书接在回答里；
  钉钉没开「企业内机器人发送消息」权限时只有文字。
- 同一个机器人只能配一个渠道。不同的群要查不同的知识库，就建不同的机器人。

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
