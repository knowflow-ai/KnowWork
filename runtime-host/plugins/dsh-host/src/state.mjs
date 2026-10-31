/**
 * 宿主内各插件共享的会话状态。同一进程内由 Node 模块缓存保证单例。
 *
 * 规则：
 * - 只有 HTTP 驱动打开会话时能建状态（createSession）；其他插件只读（getSession）。
 * - 删除按对象身份进行：只删调用方当时拿到的那份，避免旧会话的收尾删掉同 id 的新会话。
 * - 会话已删除后到达的事件直接丢弃，不重建状态。
 * - 只存运行期数据，持久事实在业务服务。
 */

import { randomUUID } from 'node:crypto'

/** 本宿主进程的实例号。宿主重启后变化，客户端据此判断游标是否还有效。 */
export const EPOCH = randomUUID()

const BUFFER_FRAMES = 2000
const BUFFER_BYTES = 8 * 1024 * 1024

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
 * @property {{ frames: object[], bytes: number }} buffer
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
 * @property {readonly string[] | undefined} bundledOff 本空间停用了的自带技能（随清单拿到）
 * @property {'zh' | 'en' | undefined} language 最近一轮的交互语言，由业务服务随消息下发
 * @property {{ bases: readonly object[], omitted: number } | undefined} knowledgeCatalog 最近一次下发的知识库目录
 * @property {string | undefined} turnContext 扩展补的常驻上下文（turn-context），最近一次下发的；空串是「现在没有」
 * @property {Map<string, string>} turnTexts 已收下、还没进入步骤的消息各自带的这一轮上下文，键是消息 id
 * @property {'agent' | 'quick'} answerMode 当前步骤按哪种回答方式（answer-mode）：随消息进入步骤切换
 * @property {Map<string, 'agent' | 'quick'>} answerModes 已收下、还没进入步骤的消息各自的回答方式，键是消息 id
 */

/** @type {Map<string, SessionState>} */
const sessions = new Map()
let frameSeq = 0

/** @returns {SessionState} */
export function createSession(sessionId, policy) {
  if (sessions.has(sessionId)) throw new Error(`会话状态已存在：${sessionId}`)
  const state = {
    id: sessionId,
    policy,
    buffer: { frames: [], bytes: 0 },
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
    language: undefined,
    knowledgeCatalog: undefined,
    turnContext: undefined,
    turnTexts: new Map(),
    answerMode: 'agent',
    answerModes: new Map(),
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

/** 当前缓冲里最早一帧的游标；缓冲为空时返回 undefined。 */
export function oldestCursor(state) {
  return state.buffer.frames[0]?.cursor
}

/** 向会话推送一帧并放进缓冲；会话不存在时丢弃。 */
export function publish(sessionId, kind, data) {
  const state = sessions.get(sessionId)
  if (state === undefined) return undefined
  const frame = { epoch: EPOCH, cursor: ++frameSeq, kind, time: Date.now(), data }
  const size = Buffer.byteLength(JSON.stringify(frame))
  const buffer = state.buffer
  buffer.frames.push({ ...frame, size })
  buffer.bytes += size
  while (buffer.frames.length > BUFFER_FRAMES || buffer.bytes > BUFFER_BYTES) {
    const dropped = buffer.frames.shift()
    buffer.bytes -= dropped.size
  }
  for (const subscriber of [...state.subscribers]) {
    if (!subscriber.write(frame)) subscriber.end()
  }
  return frame
}

/**
 * 推一帧临时帧：只给当前订阅方，不进回放缓冲、不占新游标（带的是当前最后一个游标）。
 * 用于官方逐字输出：片段量大、只对正在看的人有用，断线重连后由这一步的正式消息补齐。
 */
export function publishLive(sessionId, kind, data) {
  const state = sessions.get(sessionId)
  if (state === undefined || state.subscribers.size === 0) return undefined
  const frame = { epoch: EPOCH, cursor: frameSeq, kind, time: Date.now(), data }
  for (const subscriber of [...state.subscribers]) {
    if (!subscriber.write(frame)) subscriber.end()
  }
  return frame
}

/** 缓冲里游标大于 after 的帧（去掉内部字段）。 */
export function framesAfter(state, after) {
  return state.buffer.frames.filter(f => f.cursor > after).map(({ size, ...frame }) => frame)
}
