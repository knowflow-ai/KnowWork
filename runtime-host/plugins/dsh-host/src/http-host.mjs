/**
 * KnowWork 宿主的 HTTP 协议驱动。
 *
 * 形态照官方 `@deepseek-ai/dsh-sdk-jsonrpc-server`：插件拿到 `ctx.agents`，把外部请求映射成
 * `agents.create / resume`、`agent.followup / steer / cancel`，并转发 `session/event`。
 * 同官方一样：同一会话「已打开或正在打开」时拒绝再开；等整棵插件树就绪后才对外服务；
 * 关闭时先拒新请求、等正在打开的会话、排空子 Agent，再释放。
 * 区别只在传输：官方是 stdio JSON-RPC，这里是带令牌的 HTTP，事件流为 NDJSON。
 * 合同本身（开会话、每一轮的请求体）怎么收在 intake.mjs，用户设备上的本机宿主收同一份；这里只管 HTTP 与 Agent 的生灭。
 * 空闲会话定时释放（见 idle-sessions.mjs），业务服务下次发消息走恢复流程。
 *
 * 业务服务是唯一调用方；宿主只监听内网地址，缺令牌或令牌太短直接拒绝启动。
 */

import { createServer } from 'node:http'
import { readPluginInventory } from '@deepseek-ai/dsh-host-plugin-inventory'
import { setActivationToken } from './multi-user/activation.mjs'
import { UnknownPlugin, createPluginPresets } from './plugin-presets.mjs'
import { createCommands } from './commands.mjs'
import { createRequire } from 'node:module'
import { createHash, timingSafeEqual } from 'node:crypto'
import { brandString } from '@deepseek-ai/dsh-brand'
import { credentialRef } from '@deepseek-ai/dsh-credentials'
import { createAnswers } from './answers.mjs'
import { hostMount } from './workpath.mjs'
import { EPOCH, LIVE_SOURCE, ROOT_SOURCE, childIdOfKey, childSessionsOf, deliver, dropSession, getSession, mergeByTime, publish, publishLive, resolveRootSession, sourceOf } from './state.mjs'
import { createIdleTracker, idleMsOf, releasable, sweepMsOf } from './idle-sessions.mjs'
import { bundledCandidates } from './bundled-skills.mjs'

import { deploymentTimeZone } from './message-source.mjs'
import { liveFrame } from './live-frame.mjs'
import { createModelCatalog, modelListOf } from './model-catalog.mjs'
import { HostError as HttpError, checkSessionId, createTurnIntake, provision, requireString, sessionContractOf, toolTokenOf } from './intake.mjs'

// DSH 版本号随 /health 一起报给业务服务，系统信息页显示（所有 @deepseek-ai 包锁在同一版本）
const DSH_VERSION = createRequire(import.meta.url)('@deepseek-ai/dsh/package.json').version

/** 官方会话日志里的一个事件 → 推给业务服务的帧：根会话的是 session/event，子 Agent 的是 subagent/event（带子会话编号） */
function durableFrame(root, sessionId, event) {
  const own = sessionId === root.id
  return {
    epoch: sourceOf(root, sessionId), cursor: event.seq, kind: own ? 'session/event' : 'subagent/event',
    time: event.time ?? Date.now(), data: own ? event : { sessionId, event },
  }
}

// 子会话的写法见 state.mjs 的 childKeyOf：原样的编号（同业务服务 device.py 的 SUB_SESSION），或「~」加 40 位摘要；
// 来源整个存进 run_event.host_epoch，最长 64
const SOURCE = /^host(:([A-Za-z0-9_.-]{1,56}|~[0-9a-f]{40}))?$/
const MAX_POSITIONS = 500

/** 事件流的位置参数：<来源>:<序号>，来源是 host 或 host:<子会话>；不合规的整个请求拒绝 */
function positionsOf(values) {
  if (values.length > MAX_POSITIONS) throw new HttpError(400, 'invalid_request', `after 最多 ${MAX_POSITIONS} 个`)
  const positions = new Map()
  for (const value of values) {
    const at = value.lastIndexOf(':')
    const source = value.slice(0, at)
    const seq = Number(value.slice(at + 1))
    if (at < 0 || !SOURCE.test(source) || !Number.isSafeInteger(seq) || seq < -1) {
      throw new HttpError(400, 'invalid_request', `after 格式不对：${value.slice(0, 80)}`)
    }
    positions.set(source, seq)
  }
  return positions
}

export const name = 'knowwork-http-host'
export const inject = ['agents']

const HEARTBEAT_MS = 2000
const CANCEL_SETTLE_MS = 5000
const MAX_BODY_BYTES = 1024 * 1024
const MAX_SUBSCRIBERS_PER_SESSION = 8
const CREDENTIAL_ACTIONS = new Set(['tool-credential', 'skill-credential'])

const digest = value => createHash('sha256').update(value).digest()

function tokenMatches(expectedDigest, header) {
  if (typeof header !== 'string' || !header.startsWith('Bearer ')) return false
  return timingSafeEqual(digest(header.slice(7)), expectedDigest)
}

async function readJson(req) {
  const chunks = []
  let size = 0
  for await (const chunk of req) {
    size += chunk.length
    if (size > MAX_BODY_BYTES) throw new HttpError(413, 'body_too_large', '请求体过大')
    chunks.push(chunk)
  }
  if (chunks.length === 0) return {}
  try {
    return JSON.parse(Buffer.concat(chunks).toString('utf8'))
  } catch {
    throw new HttpError(400, 'invalid_json', '请求体不是合法 JSON')
  }
}

function send(res, status, body) {
  res.writeHead(status, { 'content-type': 'application/json; charset=utf-8' })
  res.end(JSON.stringify(body))
}

function sessionIdOf(raw) {
  let id
  try {
    id = decodeURIComponent(raw)
  } catch {
    throw new HttpError(400, 'invalid_request', '路径参数编码错误')
  }
  return checkSessionId(id)
}

function segmentOf(raw) {
  try {
    return decodeURIComponent(raw)
  } catch {
    throw new HttpError(400, 'invalid_request', '路径参数编码错误')
  }
}

/**
 * @param {import('@deepseek-ai/cordis').Context} ctx
 * @param {{ host?: string, port?: number, token?: string, provider?: string, model?: string, modelTokenRef?: string,
 *   idleMinutes?: number, plugins?: Record<string, { rows: object[] }> }} [config] plugins：目录之外再认的预置插件
 */
export function apply(ctx, config) {
  config ??= {}
  const log = ctx.logger
  const token = config.token ?? process.env.KNOWWORK_HOST_TOKEN
  if (typeof token !== 'string' || token.length < 32) {
    throw new Error('knowwork-http-host: 需要至少 32 个字符的宿主令牌（KNOWWORK_HOST_TOKEN）')
  }
  const tokenDigest = digest(token)
  const host = config.host ?? '127.0.0.1'
  const port = config.port ?? Number(process.env.KNOWWORK_HOST_PORT ?? 0)
  const agentOptions = {
    provider: config.provider ?? process.env.KNOWWORK_MODEL_PROVIDER ?? 'knowwork-gateway',
    model: config.model ?? process.env.KNOWWORK_MODEL ?? 'default',
  }
  const modelTokenRef = config.modelTokenRef ?? process.env.KNOWWORK_MODEL_TOKEN_REF ?? 'KNOWWORK_MODEL_TOKEN'
  // 可选的对话模型（model-catalog.mjs）：业务服务推清单，会话按官方 agent.model.select 选
  const models = createModelCatalog(ctx, {
    entryId: config.modelEntry ?? process.env.KNOWWORK_MODEL_ENTRY ?? 'llm-knowwork-gateway',
    provider: agentOptions.provider,
    defaultModel: agentOptions.model,
  })
  // 收一轮（intake.mjs）：消息没带用户时区时用部署时区（见 message-source.mjs）
  const receiveTurn = createTurnIntake({ models, provider: agentOptions.provider, fallbackTimeZone: deploymentTimeZone() })

  /** @type {Map<string, import('@deepseek-ai/dsh-agent').AgentHandle>} */
  const handles = new Map()
  /** @type {Map<string, Promise<unknown>>} */
  const opening = new Map()
  const closing = new Set()
  let shuttingDown = false
  const idleMs = idleMsOf(config.idleMinutes ?? process.env.KNOWWORK_HOST_SESSION_IDLE_MINUTES)
  const idle = createIdleTracker(idleMs)
  /** 整台宿主最后一次被对话用到的时间（GET /v1/activity 报给业务服务，专属宿主据此判断空闲、回收）。规则同会话的空闲 */
  let lastActive = Date.now()
  // 按会话的预置插件装官方预设（plugin-presets.mjs）
  const presets = createPluginPresets(ctx, config.plugins)
  // 用户的 / 命令（commands.mjs，官方 dsh-commands，环境层里挂着）
  const commands = createCommands(() => ctx.get('commands'))

  // 官方会话日志里的事件（根会话的，和它的子 Agent 的）推给这段会话的订阅方：游标是官方序号，按来源各记各的
  ctx.on('session/event', (session, event) => {
    const root = resolveRootSession(session)
    if (root !== undefined) deliver(root, durableFrame(root, String(session.id), event))
  })
  // 长任务目标的自动续跑开着没有（官方 dsh-goal 只记在内存里：会话重开、宿主重启后先关上，要用户「恢复」才再开）：
  // 转给业务服务，目标面板据此写「待继续」、给「恢复」按钮。没有目标的会话不转
  ctx.on('goal/activation-changed', ({ sessionId, goal }) => {
    if (goal === undefined) return
    publish(String(sessionId), 'goal/activation', { goalId: String(goal.id), revision: goal.revision, armed: goal.activation === 'armed' })
  })
  ctx.on('agent/status', ({ agent, status }) => {
    const sessionId = String(agent.session.id)
    if (handles.has(sessionId)) {
      idle.touch(sessionId)  // 一轮跑完（变成空闲）从这一刻起算
      lastActive = Date.now()
    }
    publish(sessionId, 'agent/status', { status })
  })
  // 逐字输出：官方 dsh-agent-loop 的 agent/assistant-stream（官方会话接口 dsh-api-session-controller、
  // 命令行运行 dsh-headless 都订阅它出字）。转正文与思考片段，和模型开始写某个工具调用时的工具名，作为临时帧推给当前订阅方；
  // 这一步的正式消息照常落成会话事件，业务服务拿它整段替换。子 Agent 的会话不是宿主打开的，查不到就丢。
  const announced = new Map() // 每次输出里已经报过名字的工具调用（attemptId → 序号）：参数片段很多，名字只报一次
  ctx.on('agent/assistant-stream', ({ agent, frame }) => {
    const data = liveFrame(frame, announced)
    if (data !== undefined) publishLive(String(agent.session.id), 'assistant/stream', data)
  }, { global: true })

  function liveAgent(sessionId) {
    const handle = handles.get(sessionId)
    if (closing.has(sessionId) || handle === undefined || ctx.agents.get(handle.agent.id) !== handle.agent) {
      throw new HttpError(404, 'session_not_found', `会话不存在或已释放：${sessionId}`)
    }
    return handle.agent
  }

  async function openSession(body) {
    if (shuttingDown) throw new HttpError(503, 'shutting_down', '宿主正在关闭')
    const contract = sessionContractOf(body)
    const { sessionId, resume } = contract
    if (handles.has(sessionId) || opening.has(sessionId) || closing.has(sessionId) || getSession(sessionId) !== undefined) {
      throw new HttpError(409, 'session_busy', `会话已打开、正在打开或正在释放：${sessionId}`)
    }
    let composition
    try {
      composition = await presets.compose(contract.plugins)
    } catch (error) {
      if (error instanceof UnknownPlugin) throw new HttpError(400, 'plugin_unknown', error.message)
      log.warn('composing plugins for session %s failed: %s', sessionId, error instanceof Error ? error.stack : String(error))
      throw new HttpError(400, 'session_open_failed', '会话打开失败，详情见宿主日志')
    }
    // 等预设登记的这一下，同一个会话可能又来了一次打开
    if (handles.has(sessionId) || opening.has(sessionId) || closing.has(sessionId) || getSession(sessionId) !== undefined) {
      throw new HttpError(409, 'session_busy', `会话已打开、正在打开或正在释放：${sessionId}`)
    }
    const state = provision(contract)
    const id = brandString(sessionId)
    const { preset, setup } = composition
    const task = resume
      ? ctx.agents.resume({ resumeSessionId: id, agentOptions, ...setup === undefined ? {} : { setup } })
      : ctx.agents.create({
          sessionId: id,
          meta: { cwd: hostMount(sessionId).root, ...preset === undefined ? {} : { agentPreset: preset } },
          agentOptions,
          ...setup === undefined ? {} : { setup },
        })
    opening.set(sessionId, task)
    try {
      const handle = await task
      handles.set(sessionId, handle)
      state.agent = handle.agent
      idle.touch(sessionId)
      lastActive = Date.now()
      return { sessionId, status: handle.agent.status, epoch: EPOCH }
    } catch (error) {
      dropSession(state)
      log.warn('open session %s failed: %s', sessionId, error instanceof Error ? error.stack : String(error))
      throw new HttpError(400, 'session_open_failed', '会话打开失败，详情见宿主日志')
    } finally {
      opening.delete(sessionId)
    }
  }

  function sendMessage(sessionId, body) {
    return receiveTurn(liveAgent(sessionId), getSession(sessionId), body)
  }

  async function syncModels(body) {
    try {
      modelListOf(body.models)
    } catch (error) {
      throw new HttpError(400, 'invalid_request', error.message)
    }
    try {
      return await models.sync(body.models)
    } catch (error) {
      log.warn('syncing models failed: %s', error instanceof Error ? error.stack : String(error))
      throw new HttpError(503, 'models_unavailable', '模型清单没能更新，详情见宿主日志')
    }
  }

  /**
   * 用户在我们的页面上改了对话标题：照官方改名写进官方会话标题（来源是用户，之后官方不再自动改它）。
   * 对话标题以官方为准：官方按第一句话、再用模型起标题，业务服务从会话日志里的 session/title 同步过去。
   */
  function retitle(sessionId, body) {
    const title = body !== null && typeof body === 'object' ? body.title : undefined
    if (typeof title !== 'string' || title.trim().length === 0 || title.length > 200) {
      throw new HttpError(400, 'invalid_request', 'title 必须是 1 到 200 个字符的字符串')
    }
    const titles = ctx.get('sessionTitle')
    if (titles === undefined) throw new HttpError(409, 'title_unavailable', '宿主没有挂官方会话标题服务')
    try {
      return { sessionId, title: titles.rename(liveAgent(sessionId).session, title).title }
    } catch (error) {
      if (error instanceof HttpError) throw error
      throw new HttpError(400, 'invalid_request', error instanceof Error ? error.message : String(error))
    }
  }

  /**
   * 长任务目标此刻的状态，含自动续跑开着没有（官方 dsh-goal 只记在内存里：会话重开、宿主重启后先关上，要用户「恢复」才再开）。
   * 开关的变化照官方是进程内的帧（goal/activation，不带序号、不补积压），业务服务的目标面板要知道时来这里现取。没有目标给 null
   */
  function goalState(sessionId) {
    const goal = ctx.get('goals')?.get(liveAgent(sessionId))
    if (goal === undefined || goal === null) return { goal: null }
    return { goal: { goalId: String(goal.id), revision: goal.revision, phase: goal.phase, armed: goal.activation === 'armed' } }
  }

  async function cancel(sessionId) {
    const agent = liveAgent(sessionId)
    agent.cancel({ kind: 'user' })
    let timer
    const settled = await Promise.race([
      agent.whenIdle().then(() => true),
      new Promise(resolve => { timer = setTimeout(() => resolve(false), CANCEL_SETTLE_MS) }),
    ])
    clearTimeout(timer)
    return { settled, status: agent.status }
  }

  async function dispose(sessionId) {
    if (closing.has(sessionId)) throw new HttpError(409, 'session_busy', `会话正在释放：${sessionId}`)
    const handle = handles.get(sessionId)
    if (handle === undefined) throw new HttpError(404, 'session_not_found', `会话不存在：${sessionId}`)
    closing.add(sessionId)
    const state = getSession(sessionId)
    try {
      // 先结算待决审批、结束事件流，再释放 agent；否则释放会等一个最长 30 分钟的审批。
      if (state !== undefined) dropSession(state)
      handles.delete(sessionId)
      idle.forget(sessionId)
      await handle.dispose()
      return { disposed: true }
    } finally {
      closing.delete(sessionId)
    }
  }

  /**
   * 事件流：游标是官方会话日志的序号，按来源各记各的（根会话 host，子 Agent host:<子会话>），参数 after=<来源>:<序号> 可给多个。
   * 照官方会话控制器 follow 的拼法：先读位置之后的积压（官方会话的 snapshotEvents），再接实时事件，按序号去重，不断档；
   * 宿主重启后位置照旧有效。开头先给一帧 host/instance（宿主的实例号）：业务服务只用它判断要不要补发令牌；积压之后给一帧此刻的 Agent 状态。
   * 进程内的帧（来源 live）不带序号、不补积压。
   */
  function streamEvents(sessionId, url, req, res) {
    const state = getSession(sessionId)
    if (state === undefined || closing.has(sessionId)) throw new HttpError(404, 'session_not_found', `会话不存在：${sessionId}`)
    if (state.subscribers.size >= MAX_SUBSCRIBERS_PER_SESSION) throw new HttpError(429, 'too_many_subscribers', '订阅过多')
    const positions = positionsOf(url.searchParams.getAll('after'))

    res.writeHead(200, { 'content-type': 'application/x-ndjson; charset=utf-8', 'cache-control': 'no-store' })
    const line = frame => res.write(`${JSON.stringify(frame)}\n`)
    let ended = false
    const subscriber = {
      write: frame => {
        if (ended) return false
        if (frame.epoch !== LIVE_SOURCE) {
          if (frame.cursor <= (positions.get(frame.epoch) ?? -1)) return true
          positions.set(frame.epoch, frame.cursor)
        }
        return line(frame)
      },
      end: () => {
        if (ended) return
        ended = true
        clearInterval(heartbeat)
        state.subscribers.delete(subscriber)
        res.end()
      },
    }
    line({ epoch: LIVE_SOURCE, cursor: 0, kind: 'host/instance', time: Date.now(), data: { instance: EPOCH } })
    // 积压与挂上订阅在同一段同步代码里：官方日志事件在提交后同步派发，中间插不进新事件；重叠的由上面按序号去重
    for (const frame of backlog(state, positions)) subscriber.write(frame)
    // 积压之后给一帧此刻的 Agent 状态（同官方会话控制器开场快照里的 running）：状态是进程内的帧、不补积压，
    // 续读的一方靠它知道还在不在跑——积压里有 turn/start 不等于现在还在跑
    line({ epoch: LIVE_SOURCE, cursor: 0, kind: 'agent/status', time: Date.now(), data: { status: state.agent?.status ?? 'idle' } })
    const heartbeat = setInterval(() => { if (!res.write('\n')) subscriber.end() }, HEARTBEAT_MS)
    state.subscribers.add(subscriber)
    req.on('close', () => subscriber.end())
  }

  /**
   * 根会话与它的子 Agent（内存里还记着的、业务服务带来位置的）在位置之后的官方事件：每个来源按官方序号，来源之间按时间穿插
   * （mergeByTime）
   */
  function backlog(state, positions) {
    const children = new Set(childSessionsOf(state.id))
    for (const source of positions.keys()) {
      const child = source.startsWith(`${ROOT_SOURCE}:`) ? childIdOfKey(source.slice(ROOT_SOURCE.length + 1)) : undefined
      if (child !== undefined) children.add(child)
    }
    const read = (sessionId, session) => {
      if (session === undefined) return []
      const from = (positions.get(sourceOf(state, sessionId)) ?? -1) + 1
      return session.snapshotEvents(from).map(event => durableFrame(state, sessionId, event))
    }
    // 子 Agent 不在内存里了（跑完释放、宿主重启过）就读不到；它的结果在根会话自己的日志里
    return mergeByTime([read(state.id, state.agent?.session), ...[...children].map(child => read(child, ctx.agents.get(child)?.session))])
  }

  /** 模型网关短期令牌轮换：写入官方凭据服务，下一次模型请求即使用新值。 */
  async function rotateModelCredential(body) {
    const credentials = ctx.get('credentials')
    if (credentials === undefined) throw new HttpError(503, 'credentials_unavailable', '宿主没有挂载凭据服务')
    const value = requireString(body.token, 'token')
    if (value.length < 16 || value.length > 4096) throw new HttpError(400, 'invalid_request', 'token 长度不合法')
    await credentials.set(credentialRef(modelTokenRef), value)
    return { rotated: true }
  }

  /** 个人专属宿主请业务服务激活对话用的令牌（按这个人签，只能激活他自己的对话）；共享宿主不会收到，照旧用宿主令牌 */
  function rotateActivationCredential(body) {
    const value = requireString(body.token, 'token')
    if (value.length < 16 || value.length > 4096) throw new HttpError(400, 'invalid_request', 'token 长度不合法')
    setActivationToken(value)
    return { rotated: true }
  }

  /**
   * 回调令牌续期：只对已带令牌打开的会话生效，下一次工具调用或技能加载即使用新值。
   * @param {'toolAccess' | 'skillAccess'} slot
   */
  function rotateAccess(sessionId, body, slot) {
    const state = getSession(sessionId)
    if (state === undefined || closing.has(sessionId)) throw new HttpError(404, 'session_not_found', `会话不存在：${sessionId}`)
    if (state[slot] === undefined) {
      throw slot === 'toolAccess'
        ? new HttpError(409, 'tools_not_enabled', '该会话打开时没有启用工具桥')
        : new HttpError(409, 'skills_not_enabled', '该会话打开时没有启用技能')
    }
    state[slot] = { token: toolTokenOf(body.token, 'token') }
    return { rotated: true }
  }

  // 审批与提问的决定经业务服务交来，收法和本机宿主经主进程收的是同一份（answers.mjs）
  const answers = createAnswers(ctx, { agentOf: liveAgent })

  /**
   * 宿主自带的技能：业务服务的「技能」页、@ 菜单与「公共与自带」页要列出来，业务服务自己不知道有哪些。
   * 自带技能按会话注册（bundled-skills），这里给的是全部，停用与同名由业务服务按空间算。
   */
  /**
   * 宿主里的插件和登记了的工具：业务服务试装上传的插件时读（hosts/trial.py）。插件状态用官方插件清单的读法
   * （@deepseek-ai/dsh-host-plugin-inventory 的 readPluginInventory，每次直接读加载器：激活了、失败了带原因）；
   * 工具是全局登记处里此刻看得到的名字（官方登记处不记是谁登记的，业务服务拿不带插件的宿主当基线比出新增的）。
   */
  async function pluginInventory() {
    const { entries } = await readPluginInventory(ctx)
    const tools = ctx.get('tools')?.definitions().map(definition => definition.name) ?? []
    return {
      entries: entries.map(({ entryId, moduleName, enabled, fiberPhase, diagnostic }) =>
        ({ entryId, moduleName, enabled, fiberPhase, ...diagnostic === undefined ? {} : { diagnostic } })),
      tools: [...new Set(tools)].sort(),
    }
  }

  function bundledSkills() {
    return {
      skills: bundledCandidates().map(skill => ({
        name: skill.name,
        description: skill.description,
        userInvocable: skill.invocation.userInvocable,
      })),
    }
  }

  /**
   * 这台宿主在不在用：idleMs 是离最后一次被对话用到过了多久（续签令牌、查状态不算），busy 是还没真正停下来的会话数
   * （正在跑、等审批或提问、有订阅者、名下有后台任务，同空闲会话的释放条件）。个人专属宿主由业务服务按它回收：
   * 几个业务服务进程看到的是同一份，不用各记各的。
   */
  function activity() {
    const jobs = ctx.get('jobs')
    const busy = [...handles].filter(([sessionId, handle]) => !releasable(handle.agent, getSession(sessionId), jobs))
    return { idleMs: Date.now() - lastActive, sessions: handles.size + opening.size, busy: busy.length + opening.size }
  }

  async function route(req, res) {
    const url = new URL(req.url ?? '/', 'http://host')
    const path = url.pathname
    if (req.method === 'GET' && path === '/health') return send(res, 200, { ok: !shuttingDown, epoch: EPOCH, dsh: DSH_VERSION })
    if (!tokenMatches(tokenDigest, req.headers.authorization)) throw new HttpError(401, 'unauthorized', '令牌无效')

    if (req.method === 'POST' && path === '/v1/sessions') return send(res, 201, await openSession(await readJson(req)))
    if (req.method === 'PUT' && path === '/v1/model-credential') return send(res, 200, await rotateModelCredential(await readJson(req)))
    if (req.method === 'PUT' && path === '/v1/activation-credential') return send(res, 200, rotateActivationCredential(await readJson(req)))
    if (req.method === 'PUT' && path === '/v1/models') return send(res, 200, await syncModels(await readJson(req)))
    if (req.method === 'GET' && path === '/v1/skills/bundled') return send(res, 200, bundledSkills())
    if (req.method === 'GET' && path === '/v1/plugins') return send(res, 200, await pluginInventory())
    if (req.method === 'GET' && path === '/v1/activity') return send(res, 200, activity())

    const match = /^\/v1\/sessions\/([^/]+)(?:\/([^/]+)(?:\/([^/]+))?)?$/.exec(path)
    if (match === null) throw new HttpError(404, 'not_found', '路径不存在')
    const sessionId = sessionIdOf(match[1])
    const action = match[2]
    const sub = match[3]
    // 业务服务定期续签令牌、查状态不算活动：否则会话永远不空闲
    if (handles.has(sessionId) && !CREDENTIAL_ACTIONS.has(action) && !(req.method === 'GET' && action === undefined)) {
      idle.touch(sessionId)
      lastActive = Date.now()
    }

    if (req.method === 'DELETE' && action === undefined) return send(res, 200, await dispose(sessionId))
    if (req.method === 'GET' && action === undefined) return send(res, 200, { sessionId, status: liveAgent(sessionId).status })
    if (req.method === 'POST' && action === 'messages') return send(res, 202, sendMessage(sessionId, await readJson(req)))
    if (req.method === 'POST' && action === 'cancel') return send(res, 200, await cancel(sessionId))
    if (req.method === 'PUT' && action === 'title') return send(res, 200, retitle(sessionId, await readJson(req)))
    if (req.method === 'GET' && action === 'goal') return send(res, 200, goalState(sessionId))
    if (req.method === 'PUT' && action === 'tool-credential') return send(res, 200, rotateAccess(sessionId, await readJson(req), 'toolAccess'))
    if (req.method === 'PUT' && action === 'skill-credential') return send(res, 200, rotateAccess(sessionId, await readJson(req), 'skillAccess'))
    if (req.method === 'GET' && action === 'events') return streamEvents(sessionId, url, req, res)
    if (req.method === 'GET' && action === 'commands') return send(res, 200, commands.list(liveAgent(sessionId)))
    if (req.method === 'POST' && action === 'commands') {
      return send(res, 200, await commands.run(liveAgent(sessionId), await readJson(req)))
    }
    if (req.method === 'GET' && action === 'approvals' && sub === undefined) return send(res, 200, answers.listApprovals(sessionId))
    if (req.method === 'POST' && action === 'approvals' && sub !== undefined) {
      const body = await readJson(req)
      return send(res, 200, await answers.decide(sessionId, segmentOf(sub), body?.outcome))
    }
    if (req.method === 'GET' && action === 'questions' && sub === undefined) return send(res, 200, answers.listQuestions(sessionId))
    if (req.method === 'POST' && action === 'questions' && sub !== undefined) {
      return send(res, 200, answers.answerQuestion(sessionId, segmentOf(sub), await readJson(req)))
    }
    throw new HttpError(404, 'not_found', '路径不存在')
  }

  const server = createServer((req, res) => {
    route(req, res).catch(error => {
      if (!(error instanceof HttpError)) log.error('request %s %s failed: %s', req.method, req.url, error instanceof Error ? error.stack : String(error))
      const status = error instanceof HttpError ? error.status : 500
      const code = error instanceof HttpError ? error.code : 'internal'
      const message = error instanceof HttpError ? error.message : '宿主内部错误'
      if (!res.headersSent) send(res, status, { error: { code, message } })
      else res.end()
    })
  })
  server.on('error', error => {
    log.error('http server error: %s', error instanceof Error ? error.stack : String(error))
    process.exitCode = 1
  })

  /** 释放空闲会话：一次一个，出错只记日志（下一轮再试）。 */
  async function sweepIdle() {
    if (shuttingDown) return
    const jobs = ctx.get('jobs')
    for (const [sessionId, handle] of [...handles]) {
      if (opening.has(sessionId) || closing.has(sessionId) || !idle.due(sessionId)) continue
      if (!releasable(handle.agent, getSession(sessionId), jobs)) continue
      try {
        await dispose(sessionId)
        log.info('session %s idle for %d min; released (reopens on the next message)', sessionId, Math.round(idleMs / 60_000))
      } catch (error) {
        log.warn('release idle session %s failed: %s', sessionId, error instanceof Error ? error.message : String(error))
      }
    }
  }

  ctx.effect(() => {
    if (idleMs === 0) return () => {}
    let sweeping = false
    const timer = setInterval(() => {
      if (sweeping) return
      sweeping = true
      sweepIdle().finally(() => { sweeping = false })
    }, sweepMsOf(idleMs))
    timer.unref?.()
    return () => clearInterval(timer)
  }, 'knowwork.http-host.idle-sweep')

  ctx.effect(() => {
    // 照官方就绪边界：整棵插件树（会话策略、技能门、审批应答者等监听）挂好之后才对外服务。
    const ready = (async () => {
      await ctx.get('loader')?.await()
      if (shuttingDown) return
      await new Promise(resolve => server.listen(port, host, resolve))
      const address = server.address()
      const bound = typeof address === 'object' && address ? address.port : port
      process.stdout.write(`${JSON.stringify({ knowworkHostReady: true, host, port: bound, epoch: EPOCH })}\n`)
    })().catch(error => {
      log.error('host failed to start: %s', error instanceof Error ? error.stack : String(error))
      process.exitCode = 1
    })

    return async () => {
      // 照官方关闭顺序：拒新请求 → 等正在打开的会话 → 排空子 Agent → 释放 → 清状态。
      shuttingDown = true
      await ready
      server.closeAllConnections?.()
      await new Promise(resolve => server.close(() => resolve(undefined)))
      await Promise.allSettled([...opening.values()])
      const live = [...handles.entries()]
      handles.clear()
      await Promise.allSettled([ctx.get('subagents')?.drainDescendants(live.map(([, h]) => h.agent))])
      for (const [sessionId] of live) {
        const state = getSession(sessionId)
        if (state !== undefined) dropSession(state)
      }
      await Promise.allSettled(live.map(([, handle]) => handle.dispose()))
    }
  }, 'knowwork.http-host')
}

export { liveFrame }
