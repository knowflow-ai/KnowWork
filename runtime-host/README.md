# KnowWork 运行时宿主

基于 DeepSeek Harness（DSH）官方配置档机制的宿主：

- `profile/`：宿主配置档模板，组合 `@deepseek-ai/dsh-sdk-minimal` 与 `@knowwork/dsh-host-bundle`。
- `plugins/host-bundle/`：KnowWork 配置层，停用官方 stdio 协议与本机 shell，插入 KnowWork 插件、官方审批服务、官方提问（`ask_user_question`）与待办（`todo_write`）、官方技能注册表与 `skill` 工具。
- `plugins/dsh-host/`：KnowWork 插件（HTTP 驱动、审批与提问转发、会话工具策略、技能门、技能提供方与专业工具解锁、工具桥、子 Agent 策略继承、测试工具）。
- `tests/`：E1 准入测试，真实 DSH 组合加官方 `dsh-llm-mock-server`。

## 运行

需要 Node 22.19 以上（本机用 nvm 的 v24.18.0）。

```sh
npm install
npm test
```

DSH 所有包锁定在 0.2.1-rc.2。

## 技能

会话打开时业务服务在建会话请求里给出 `skills: { token }`（受众 skills 的短期令牌），宿主据此：

1. 拉本会话可用技能清单（`GET /internal/extensions/skills/catalog`，地址取 `KNOWWORK_SKILLS_BASE_URL`）；
   拉取失败或清单不合格，会话打开失败；
2. 用官方 `createScope(ctx, agent)` 按 agent 注册技能提供方，官方 `dsh-tool-skill` 出目录与 `skill` 工具；
   模型加载技能时才拉正文（`GET /content?name=&digest=`），按清单里的 `contentDigest` 校验；
3. 被技能认领的专业工具（清单里的 `unlocks`）在加载该技能之前不注册、不可见、不可执行；
   经 `skill` 工具加载或用户 `/技能名` 显式调用之后解锁；
4. 加载过的技能把 `completionTools` 交给技能门：本轮任一完成工具没成功，最终回答被拦下。

令牌续期：`PUT /v1/sessions/{id}/skill-credential`。

## 向用户提问与待办

官方 `dsh-tool-ask-user` 用 timed 模式（legacy 模式的请求不带工具调用 id，问题也不进官方投影，宿主重启就丢）。
提问应答者 `question-broker` 照审批应答者的形态，把问题以 `question/requested` 帧推给业务服务，用户作答后：

- `GET /v1/sessions/{id}/questions`：待答问题，`state` 为 `open`（前台等待中）或 `continued`（等待已结束，仍可答）；
- `POST /v1/sessions/{id}/questions/{callId}`，请求体 `{ answers: [{ id, selected, custom? }] }`：回答先按原问题核对
  （选项存在、单选最多一个、每题恰好一条）。`open` 的直接交回工具；`continued` 的经官方 `userQuestions.answer`
  以 `user-question-reply` 消息送回，会话空闲时开新一轮。

前台等待上限 `KNOWWORK_QUESTION_TIMEOUT_MS`（默认 30 分钟，同时是工具的默认期限）。到期后模型先继续，问题转入
`continued`；模型自己传 `-1`（不限时）的提问到上限按超时关闭。宿主崩溃后恢复会话，没答完的问题转入 `continued`；
宿主正常关闭（SIGTERM）时官方会把前台等待中的提问记成已中止，恢复后不能再答（`question/closed`，`reason=interrupted`），
用户改在输入框直接回复。

官方 `dsh-tool-todo` 每次整表写入，落成 `todo/write` 会话事件，业务服务据此展示待办进度。
