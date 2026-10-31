# 开发

## 准备

- Python 3.12 与 [uv](https://docs.astral.sh/uv/)；
- Node.js 24.9 以上；
- Docker：会话沙箱要用。不需要本机装 PostgreSQL，开发与测试用嵌入式的。

## 本机拉起全套

```sh
cd runtime-host && npm install && cd ..
cd web && npm install && cd ..
docker build -f deploy/docker/sandbox.Dockerfile -t knowwork-sandbox:dev .   # 会话沙箱镜像，第一次要构建
cd server && uv sync && uv run python ../scripts/dev_stack.py
```

浏览器打开 http://127.0.0.1:5173 。脚本起了嵌入式 PostgreSQL、运行时宿主（7300）、业务服务（8000 公开、8001 内部）、
后台任务 worker（附件解析，业务服务就绪后再起）和前端开发服务器（5173，`/api` 代理到 8000），Ctrl+C 一起停。
要解析 PDF、Office 附件时，到「管理 → 模型 → 文档解析」里填 MinerU 地址（也可以启动前设 `KNOWWORK_MINERU_BASE_URL` 当默认值）。

- 改了代码不用重启整套：前端热更新；业务服务与 worker 的 Python 代码、宿主的插件（`runtime-host/plugins`）改了
  各自自动重启（几秒）。宿主重启时正在进行的回答会中断，要重发；`runtime-host/profile/` 改了要重启整套；
- 数据、密钥、日志都在仓库根的 `.dev/`（日志在 `.dev/logs/`），删掉它就回到全新状态；
- 首次打开走初始化向导建管理员，再到「管理 → 模型」添加模型，模型别名填 `default`；
- Node 默认取 PATH 里的，也可以用 `KNOWWORK_NODE_BIN` 指定所在目录；
- 脚本会清掉代理相关的环境变量，免得系统代理把局域网里的模型服务请求转坏。

## 检查与测试

```sh
# 业务服务
cd server
uv run ruff check
uv run pytest                                  # 全部（端到端测试会起真实宿主，先在 runtime-host 里 npm install）
uv run pytest tests/test_pump.py -k cancel     # 单个
uv run python -m knowwork_server.db.migrate revision "说明"   # 新建迁移

# 运行时宿主
cd runtime-host
npm test
node --test --test-timeout=90000 tests/skills.test.mjs        # 单个

# 前端
cd web
npm run lint && npm test && npm run build
npx vitest run src/features/admin/skills/skills.test.tsx      # 单个
```

CI 跑的就是这几项，外加编排文件校验、shellcheck 与 hadolint；按目录拆成 `.github/workflows/ci-*.yml` 四个工作流，只在相关目录有改动时跑。提交前至少把改动涉及部分的检查跑过。

## 约定

- **业务服务**：每个领域一个包，`api.py` 只做路由，不直接碰表和事件泵；上层模块只通过 `kernel/contract.py` 调宿主。
  改了表结构必须配迁移，`tests/test_migrations.py` 会比对迁移结果与模型是否一致、每张工作空间表是否开了行级安全。
- **运行时宿主**：只用 DSH 官方扩展点，不 fork DSH；新能力先看官方有没有，照官方的形态来。
  DSH 版本锁定，所有 `@deepseek-ai/*` 包同一版本，升级先跑 `npm run preflight`。
- **前端**：`src/api/` 每个资源一个文件，`src/features/<领域>/` 放页面、组件与 `queries.ts`，组件只用 `@openai/apps-sdk-ui`
  和 `src/ui/`。错误统一经 `api/http.ts` 翻成给用户看的中文。
- **测试**：业务服务测试用嵌入式 PostgreSQL；宿主测试用真实的 DSH 加官方的模拟模型服务，不 mock DSH。
  和模型输出有关的测试只断言终态、调了哪些工具、引用条数、产物格式，不断言模型原文。覆盖率目标 80%。
- **文字**：代码注释、文档、提交信息、界面文案都用中文，写成直白的人话；注释讲「为什么」，尤其是踩过的坑和约束的来源。

## 发版

版本只有一个来源：仓库根的 `VERSION`。

```sh
python scripts/bump_version.py v0.0.2     # 同步到 pyproject、package.json、锁文件、compose、.env.example 与 README 徽章
# 在 CHANGELOG.md 里写好这一版，提交后打标签
git tag v0.0.2 && git push origin v0.0.2
```

打标签会触发 `.github/workflows/release.yml`：检查标签与 `VERSION` 一致，构建镜像推到阿里云容器镜像服务，
同步 postgres、redis、rustfs，给每个镜像生成 SBOM，并建 GitHub Release。构建是增量的：每个镜像按自己的构建输入
（`IMAGE_INPUTS`）和上一个版本标签比，没变的直接给上一版打上新标签；已经同步过的依赖镜像跳过。
