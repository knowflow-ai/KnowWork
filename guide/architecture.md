# 架构

## 三个进程

```text
浏览器 ──> 网关 ──> 业务服务 公开端口 8000 ──> PostgreSQL / Redis / 对象存储
                        │      ▲
              kernel.v1 │      │ 内部端口 8001：模型调用、工具执行、技能下载、沙箱操作
                        ▼      │
                  运行时宿主 7300（Node，DSH） ──> 会话沙箱（每个会话一个 Docker 容器）
```

- **业务服务**（`server/`，Python 3.12、FastAPI、SQLAlchemy 2 异步）：账号、权限、模型目录与密钥、知识源、技能、工具、
  审批、审计、用量都在这里。浏览器只连它的公开端口。
- **运行时宿主**（`runtime-host/`，Node）：用 DeepSeek Harness 的官方配置档机制跑 Agent。宿主里不放任何密钥，也不做权限判断。
- **前端**（`web/`，React 19、TypeScript、Vite）：组件用 `@openai/apps-sdk-ui`，组件库没有的放在 `web/src/ui/`。

业务服务的内部端口只给宿主回调，部署时只绑定在 api 与宿主之间的那张网卡上，网关转发不到。

## 业务服务怎么调宿主

业务服务只通过内核合同 `kernel.v1`（`server/src/knowwork_server/kernel/contract.py`，模型都带 `schema_version`）
和宿主打交道：建会话、发一轮、取消、取事件、应答审批与提问。上层模块不直接引用宿主客户端，
`server/tests/test_architecture.py` 会检查这一点。DSH 升级时，变化收在宿主和合同的实现里。

宿主需要做敏感的事时，带业务服务签发的短期令牌回调内部端口：

| 回调 | 用途 |
|---|---|
| `/internal/model/v1` | 模型网关：按模型目录转发到厂商，记用量，单轮调用次数超限就取消 |
| `/internal/extensions/tools` | 工具桥：业务工具与 MCP 工具的执行、审批、动作回执 |
| `/internal/extensions/skills` | 本会话可用的技能清单与内容 |
| `/internal/extensions/sandbox/*` | 会话沙箱里的命令与文件读写 |

## 运行时宿主

- `runtime-host/profile/`：DSH 官方配置档模板；
- `runtime-host/plugins/host-bundle/`：用 `cordis.patch.yml` 挂上或停用官方插件。官方的本机 shell、终端、文件系统、
  子进程都停用了，换成落到会话沙箱的实现；
- `runtime-host/plugins/dsh-host/`：KnowWork 自己的插件，只用官方扩展点：

| 插件 | 作用 |
|---|---|
| `tool-bridge` | 把业务服务给的工具注册进会话，执行时回调工具桥 |
| `skill-provider`、`skill-unlock` | 把本会话的技能交给官方技能机制；加载技能后才解锁它认领的专业工具 |
| `skill-gate` | 技能的完成判据没满足时拦下最终回答，让模型补上；补救次数有上限 |
| `approval-broker`、`question-broker` | 审批与向用户提问，经业务服务转给前端 |
| `remote-fs`、`remote-subprocess` | 官方文件工具与命令执行改为落到会话沙箱 |
| `session-policy`、`subagent-policy`、`tool-deny` | 按会话收窄可用工具 |

DSH 所有 `@deepseek-ai/*` 包锁在同一个版本（当前 `0.2.1-rc.2`），升级前先跑 `npm run preflight`。

## 会话沙箱

宿主不在本机执行命令，也不碰本机文件：模型发起的命令和文件读写都经业务服务落到这个会话自己的 Docker 容器里。
企业部署是多人共用的服务器，上面有数据库连接、密钥和别人的数据，附件或知识库里的提示词注入不能碰到它。

容器按会话创建，去掉全部 Linux 能力、禁止提权，可选断网；空闲一段时间后回收，附件会在新容器里重新放好。
镜像见 `deploy/docker/sandbox.Dockerfile`。

## 事件流

宿主产生的每一帧由事件泵按会话编号，先推给浏览器，再批量写进 `run_event`。前端只消费业务服务投影出的
`events.v1` 事件，不认 DSH 的原始事件。

事件泵上挂着几个观察者，都在回合结束后的后台任务里干活，不拖慢对话：

- 用量：按步记模型调用的 token 与费用；
- 产物：收下模型用官方 `present` 明确交付的文件；
- 引用：登记本轮工具返回过的证据，回答里的引用只能指向它们；
- 技能运行事实：从这一轮的日志投影出每个技能的运行、步骤与失败阶段。

## 数据

- PostgreSQL，所有业务表带工作空间维度，用行级安全强制隔离；开源版只有一个默认工作空间。
- 迁移账号与应用账号分开，应用账号不是表的所有者，关不掉行级安全。
- 迁移在 `server/src/knowwork_server/db/migrations/versions/`，业务服务启动时自动升级，带咨询锁。
- 部署主密钥由一次性容器 `bootstrap` 生成，其余凭证由它派生；模型密钥等敏感配置加密后入库。

## 业务服务的模块

每个领域一个包，常见文件：`api.py`（路由）、`service.py`、`models.py`（表）、`__init__.py` 里的装配函数，
统一在 `app.py` 装配。

| 模块 | 内容 |
|---|---|
| `identity`、`auth`、`setup` | 账号、登录、OIDC、API Key、初始化向导 |
| `access` | 工作空间、成员、部门、用户组、资源授权（Casbin） |
| `conversations` | 会话、事件泵、落库、回放、事件投影 |
| `kernel` | 内核合同与宿主客户端 |
| `gateway`、`models` | 模型网关、模型目录、用量 |
| `tools`、`approvals` | 工具注册、MCP、工具桥、动作回执、会话授权 |
| `knowledge` | 知识源与 KnowFlow 连接器、引用 |
| `skills`、`workflows` | 技能包、草稿与发布、运行事实；固定工作流（目前只有接口） |
| `sandbox`、`attachments`、`artifacts`、`documents` | 会话沙箱、附件、产物、文档预览 |
| `audit`、`debugview`、`system`、`ops` | 审计、运行记录、系统设置与品牌、健康检查与启动 |
