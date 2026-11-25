/**
 * 宿主内各插件共享的会话状态。同一进程内由 Node 模块缓存保证单例。
 *
 * 规则：
 * - 只按业务服务的开会话合同建状态（intake.mjs 的 provision）：服务端宿主上是 HTTP 驱动打开会话前建；用户设备上的本机宿主
 *   （会话由官方会话控制器建与恢复）由它的收件入口在官方 agent/created 时建。其他插件只读（getSession）。
 * - 删除按对象身份进行：只删调用方当时拿到的那份，避免旧会话的收尾删掉同 id 的新会话。
 * - 会话已删除后到达的事件直接丢弃，不重建状态。
 * - 只存运行期数据，持久事实在业务服务。
 */

import { createHash, randomUUID } from 'node:crypto'

/** 本宿主进程的实例号，宿主重启后变化。业务服务只用它判断要不要补发令牌，不当续读的游标（游标是官方会话日志的序号）。 */
export const EPOCH = randomUUID()

/**
 * 推给业务服务的帧的来源：根会话自己的官方事件记 host，子 Agent 的记 host:<子会话>（子 Agent 是另一个会话、序号另起），
 * 游标是官方会话日志的序号；同桌面端上传事件的记法（业务服务 conversations/device.py 的 device、device:<子会话>）。
 * 进程内的帧记 live、不带序号。
 */
export const ROOT_SOURCE = 'host'
export const LIVE_SOURCE = 'live'
export const sourceOf = (state, sessionId) => (sessionId === state.id ? ROOT_SOURCE : `${ROOT_SOURCE}:${childKeyOf(sessionId)}`)

/** 子会话编号直接当来源用的写法（同业务服务 device.py 的 SUB_SESSION：来源整个存进 run_event.host_epoch，最长 64） */
const PLAIN_CHILD = /^[A-Za-z0-9_.-]{1,56}$/

/**
 * 子会话在来源里的写法：官方子会话编号一般是 UUID，原样用；官方只要求它是非空字符串，不合上面写法的换成「~」加 40 位摘要，
 * 宿主自己发出的来源宿主自己一定认（不然业务服务带着它续读，整个请求被拒，泵会一直重试）
 */
export function childKeyOf(sessionId) {
  return PLAIN_CHILD.test(sessionId) ? sessionId : `~${createHash('sha256').update(sessionId).digest('hex').slice(0, 40)}`
}

/** 来源里写的子会话 → 子会话编号：原样写的就是编号；摘要写法的反推不回来（只能从内存里的子会话算来源去对） */
export function childIdOfKey(key) {
  return PLAIN_CHILD.test(key) ? key : undefined
}

/**
 * 几个来源的积压按时间合成一串：同一个来源里保持官方序号的先后（续读按序号去重，同一来源里后到的小序号会被当成重复丢掉，
 * 不能按时间把同一来源里的打乱），来源之间按时间穿插，时间一样时先排前面的来源
 * @param {object[][]} lists 每个来源一串帧，各自已按序号从小到大
 */
export function mergeByTime(lists) {
  const merged = []
  const next = lists.map(() => 0)
  for (;;) {
    let pick = -1
    for (let i = 0; i < lists.length; i += 1) {
      if (next[i] >= lists[i].length) continue
      if (pick < 0 || lists[i][next[i]].time < lists[pick][next[pick]].time) pick = i
    }
    if (pick < 0) return merged
    merged.push(lists[pick][next[pick]])
    next[pick] += 1
  }
}

/**
 * @typedef {object} SessionPolicy
 * @property {string[]} [deny] 本会话不可见的全局工具名
 * @property {{ requiredTool?: string, maxNudges?: number }} [gate] 技能门：本轮结束前必须成功调用的工具，
 *   以及补救次数上限（也用于技能的完成判据）
 */

/**
 * @typedef {object} PendingQuestion
 * @property {string} id 工具调用 id（官方提问以它为身份，跨重启不变）
 * @property {string} sessionId
 * @property {readonly object[]} questions 官方 AskUserQuestionItem 列表
 * @property {boolean} timed 前台等待有期限（期限到了问题仍可答）
 * @property {(answer: { answers: object[] }) => void} answer 交回用户的回答
 * @property {() => void} cancel 会话释放时结算为取消
 */

/**
 * @typedef {object} Subscriber
 * @property {(frame: object) => boolean} write 写入一帧；返回 false 表示对端跟不上，应断开
 * @property {() => void} end 结束订阅
 */

/**
 * @typedef {object} SessionState
 * @property {string} id
 * @property {SessionPolicy} policy
 * @property {Set<Subscriber>} subscribers
 * @property {Map<string, { id: string, sessionId: string, toolName: string, callId?: string, reason?: string, resolve: (outcome: string) => void }>} approvals
 * @property {Map<string, PendingQuestion>} questions 正在前台等待回答的提问，键是 ask_user_question 的工具调用 id
 * @property {{ turn: number, succeeded: Set<string>, nudges: number, loaded: Map<string, readonly string[]> }} gate
 *   loaded：本轮根会话加载过的技能 → 它的 completionTools
 * @property {{ token: string } | undefined} toolAccess 工具桥回调业务服务用的短期令牌（受众 tools）
 * @property {object[] | undefined} bridgeTools 会话打开时从业务服务拉取的工具描述
 * @property {string | undefined} toolNotes 随工具清单来的一段说明（例如「这些服务用户还没连接」），写进系统提示
 * @property {{ token: string } | undefined} skillAccess 技能提供方回调业务服务用的短期令牌（受众 skills）
 * @property {readonly object[] | undefined} skillCatalog 本会话可用技能清单（已发布版本）
 * @property {Promise<readonly object[]> | undefined} skillCatalogPromise 清单拉取（只拉一次）
 * @property {Set<string>} loadedSkills 本会话（含子 Agent）加载过的技能：执行命令时报给业务服务，由它加上这些技能的环境变量。
 *   只在内存里，宿主重启后会话恢复时为空，技能再加载一次才带上
 * @property {Readonly<Record<string, string>>} skillEnv 本机执行时加进命令的技能环境变量：只有用户电脑上的本机宿主取
 *   （技能提供方的 deliverEnv），只有个人技能的值；服务端宿主不取，值在业务服务的沙箱执行接口里加
 * @property {readonly string[] | undefined} bundledOff 本空间停用了的自带技能（随清单拿到）
 * @property {'zh' | 'en' | undefined} language 最近一轮的交互语言，由业务服务随消息下发
 * @property {{ bases: readonly object[], omitted: number } | undefined} knowledgeCatalog 最近一次下发的知识库目录
 * @property {string | undefined} turnContext 扩展补的常驻上下文（turn-context），最近一次下发的；空串是「现在没有」
 * @property {string | undefined} persona 扩展给这段对话的身份（turn-context），最近一次下发的；空串是照部署人设
 * @property {string | undefined} personaShown 系统提示里现在放着的那份身份（换了才重新注册）
 * @property {(() => void) | undefined} personaOff 撤掉系统提示里那份身份
 * @property {Map<string, string>} turnTexts 已收下、还没进入步骤的消息各自带的这一轮上下文，键是消息 id
 * @property {Record<string, unknown> | undefined} extensionState 扩展随最近一轮交来的状态（按扩展名分，业务服务
 *   conversations/turn_state.py 算、用户设备上执行的由设备补），扩展自己的宿主插件读；这一轮没带就沿用上一轮的
 * @property {string} answerMode 当前步骤按哪种回答方式（answer-mode）：agent、quick 或扩展登记的；随消息进入步骤切换
 * @property {{ tools: string[], prompt: string } | undefined} answerModeSpec 扩展登记的回答方式随消息带来的放行清单与指令
 * @property {(() => void) | undefined} modeMask 当前回答方式对官方全局工具的收窄（restrict 的撤销函数）
 * @property {Map<string, { mode: string, spec: { tools: string[], prompt: string } | undefined }>} answerModes
 *   已收下、还没进入步骤的消息各自的回答方式，键是消息 id
 * @property {{ workspaceId: string, userId: string } | undefined} owner 这段对话的属主（业务服务打开会话时给）：多用户适配层
 *   （multi-user/）据此记录、过滤属主级资源；旧版业务服务不给时是 undefined，属主级插件对这个会话不可用
 * @property {object | undefined} agent 宿主打开的活 Agent（http-host 打开后填）：属主级插件到点激活会话时从这里取
 */

/** @type {Map<string, SessionState>} */
const sessions = new Map()

/** @returns {SessionState} */
export function createSession(sessionId, policy) {
  if (sessions.has(sessionId)) throw new Error(`会话状态已存在：${sessionId}`)
  const state = {
    id: sessionId,
    policy,
    subscribers: new Set(),
    approvals: new Map(),
    questions: new Map(),
    gate: { turn: -1, succeeded: new Set(), nudges: 0, loaded: new Map() },
    toolAccess: undefined,
    bridgeTools: undefined,
    toolNotes: undefined,
    skillAccess: undefined,
    skillCatalog: undefined,
    skillCatalogPromise: undefined,
    loadedSkills: new Set(),
    skillEnv: Object.freeze({}),
    language: undefined,
    knowledgeCatalog: undefined,
    turnContext: undefined,
    extensionState: undefined,
    persona: undefined,
    personaShown: undefined,
    personaOff: undefined,
    turnTexts: new Map(),
    answerMode: 'agent',
    answerModeSpec: undefined,
    modeMask: undefined,
    answerModes: new Map(),
    owner: undefined,
    agent: undefined,
  }
  sessions.set(sessionId, state)
  return state
}

export function getSession(sessionId) {
  return sessions.get(sessionId)
}

const MAX_LINEAGE = 10_000
const MAX_DEPTH = 16
/** 子会话 → 直接父会话。只记录官方子 Agent（会话头 origin 为 subagent）。 */
const lineage = new Map()

/**
 * 按会话头 `parentSession` 找到经宿主打开的根会话的状态。
 * 自己就是宿主打开的会话时返回自己；不是子 Agent、或祖先链上找不到宿主会话时返回 undefined。
 * 解析时顺带记下父子关系，中间层子 Agent 的父会话由它自己被解析时记下。
 * @param {{ id: unknown, header?: { parentSession?: unknown, origin?: string } }} session
 * @returns {SessionState | undefined}
 */
export function resolveRootSession(session) {
  const id = String(session.id)
  const own = sessions.get(id)
  if (own !== undefined) return own
  const header = session.header
  if (header?.origin === 'subagent' && header.parentSession !== undefined && !lineage.has(id)) {
    if (lineage.size >= MAX_LINEAGE) lineage.delete(lineage.keys().next().value)
    lineage.set(id, String(header.parentSession))
  }
  let current = lineage.get(id)
  for (let depth = 0; current !== undefined && depth < MAX_DEPTH; depth += 1) {
    const state = sessions.get(current)
    if (state !== undefined) return state
    current = lineage.get(current)
  }
  return undefined
}

/**
 * 按会话 id 找它所属的根会话 id（根会话就是自己；子 Agent 顺着父子记录往上找，记录在它的 agent/created 里写下）。
 * 找不到返回 undefined。远程执行据此核对：调用方只能落到自己根会话的工作目录。
 */
export function rootSessionIdOf(sessionId) {
  const id = String(sessionId)
  if (sessions.has(id)) return id
  let current = lineage.get(id)
  for (let depth = 0; current !== undefined && depth < MAX_DEPTH; depth += 1) {
    if (sessions.has(current)) return current
    current = lineage.get(current)
  }
  return undefined
}

/** 子会话释放后删掉它的父子记录。 */
export function forgetLineage(sessionId) {
  lineage.delete(String(sessionId))
}

const dropListeners = new Set()

/**
 * 会话状态删除时回调（会话 id）。流式执行据此关掉这个会话的 SSH 执行环境。返回取消订阅的函数。
 * @param {(sessionId: string) => void} listener
 */
export function onSessionDropped(listener) {
  dropListeners.add(listener)
  return () => dropListeners.delete(listener)
}

/** 删除会话状态：结算待决审批与提问、结束全部订阅。只删传入的那份对象。 */
export function dropSession(state) {
  if (sessions.get(state.id) !== state) return
  sessions.delete(state.id)
  for (const pending of [...state.approvals.values()]) pending.resolve('cancelled')
  for (const pending of [...state.questions.values()]) pending.cancel()
  for (const subscriber of [...state.subscribers]) subscriber.end()
  state.subscribers.clear()
  for (const listener of [...dropListeners]) {
    try { listener(state.id) } catch { /* 回调出错不影响释放会话 */ }
  }
}

/**
 * 官方会话日志里的事件（带官方序号的持久事件）推给这段会话的订阅方。订阅方按来源记着给到哪，积压与实时重叠的部分跳过
 * （http-host.mjs 的 streamEvents），不重复也不断档。
 */
export function deliver(state, frame) {
  for (const subscriber of [...state.subscribers]) {
    if (!subscriber.write(frame)) subscriber.end()
  }
}

/**
 * 进程内的帧（Agent 状态、子 Agent 状态、目标自动续跑的开关、逐字输出）：照官方不带序号，只推给当前订阅方，不缓冲；
 * 业务服务不靠它们续读（同官方会话控制器 follow 里不带序号的 assistant-stream 帧）。会话不存在或没人订阅时丢弃。
 */
export function publish(sessionId, kind, data) {
  const state = sessions.get(sessionId)
  if (state === undefined || state.subscribers.size === 0) return undefined
  const frame = { epoch: LIVE_SOURCE, cursor: 0, kind, time: Date.now(), data }
  for (const subscriber of [...state.subscribers]) {
    if (!subscriber.write(frame)) subscriber.end()
  }
  return frame
}

/** 逐字输出（官方 agent/assistant-stream）：同 publish，片段量大、只对正在看的人有用，断线后由这一步的正式消息补齐 */
export const publishLive = publish

/**
 * 宿主插件要留下、业务服务要逐条落库的事实（自动批准、技能加载、技能门）：照官方插件的做法写进根会话的会话日志
 * （同敏感词的 content-safety/blocked、官方 dsh-llm-retry 的 llm/retry：持久、带官方序号、不进模型看到的历史），
 * 和官方事件走同一条续读的路，业务服务按事件类型还原（conversations/pump.py 的 HOST_FACTS）。
 * 根会话没有活的 Agent 时（不应发生）退回只推给当前订阅方。
 */
export function recordFact(state, type, data) {
  const session = state.agent?.session
  if (session !== undefined) {
    session.append(type, data)
    return
  }
  publish(state.id, type, data)
}

/** 内存里记着的、属于这个根会话的子 Agent 会话（续读积压时一并读它们的官方日志） */
export function childSessionsOf(rootId) {
  return [...lineage.keys()].filter(id => rootSessionIdOf(id) === rootId)
}
