/**
 * KnowWork 宿主的 HTTP 协议驱动。
 *
 * 形态照官方 `@deepseek-ai/dsh-sdk-jsonrpc-server`：插件拿到 `ctx.agents`，把外部请求映射成
 * `agents.create / resume`、`agent.followup / steer / cancel`，并转发 `session/event`。
 * 同官方一样：同一会话「已打开或正在打开」时拒绝再开；等整棵插件树就绪后才对外服务；
 * 关闭时先拒新请求、等正在打开的会话、排空子 Agent，再释放。
 * 区别只在传输：官方是 stdio JSON-RPC，这里是带令牌的 HTTP，事件流为 NDJSON。
 *
 * 业务服务是唯一调用方；宿主只监听内网地址，缺令牌或令牌太短直接拒绝启动。
 */

import { createServer } from 'node:http'
import { createRequire } from 'node:module'
import { createHash, timingSafeEqual } from 'node:crypto'
import { brandString } from '@deepseek-ai/dsh-brand'
import { createUserMessage } from '@deepseek-ai/dsh-llm'
import { credentialRef } from '@deepseek-ai/dsh-credentials'
import { AnswerError, checkAnswer, parseAnswer } from './question-answers.mjs'
import { attachmentBlocks, attachmentsOf } from './attachment-handles.mjs'
import { sessionRoot } from './sandbox-client.mjs'
import { EPOCH, createSession, dropSession, framesAfter, getSession, oldestCursor, publish, publishLive } from './state.mjs'
import { catalogOf } from './knowledge-catalog.mjs'
import { mentionBlocks, mentionsOf } from './mentions.mjs'

// DSH 版本号随 /health 一起报给业务服务，系统信息页显示（所有 @deepseek-ai 包锁在同一版本）
const DSH_VERSION = createRequire(import.meta.url)('@deepseek-ai/dsh/package.json').version

export const name = 'knowwork-http-host'
export const inject = ['agents']

const HEARTBEAT_MS = 2000
const CANCEL_SETTLE_MS = 5000
const MAX_BODY_BYTES = 1024 * 1024
const MAX_SUBSCRIBERS_PER_SESSION = 8
const SESSION_ID_PATTERN = /^[A-Za-z0-9_.:-]{1,128}$/

class HttpError extends Error {
  constructor(status, code, message) {
    super(message)
    this.status = status
    this.code = code
  }
}

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

function requireString(value, field) {
  if (typeof value !== 'string' || value.length === 0) {
    throw new HttpError(400, 'invalid_request', `缺少字段 ${field}`)
  }
  return value
}

function sessionIdOf(raw) {
  let id
  try {
    id = decodeURIComponent(raw)
  } catch {
    throw new HttpError(400, 'invalid_request', '路径参数编码错误')
  }
  if (!SESSION_ID_PATTERN.test(id)) throw new HttpError(400, 'invalid_request', 'sessionId 只允许字母、数字和 _.:-，最长 128')
  return id
}

/** 回答校验不通过统一成 400 invalid_answer，原因写进 message（业务服务只记日志，不给用户看）。 */
function validated(check) {
  try {
    return check()
  } catch (error) {
    if (error instanceof AnswerError) throw new HttpError(400, 'invalid_answer', error.message)
    throw error
  }
}

function segmentOf(raw) {
  try {
    return decodeURIComponent(raw)
  } catch {
    throw new HttpError(400, 'invalid_request', '路径参数编码错误')
  }
}

function policyOf(raw) {
  if (raw === undefined) return {}
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) throw new HttpError(400, 'invalid_request', 'policy 必须是对象')
  const policy = {}
  if (raw.deny !== undefined) {
    if (!Array.isArray(raw.deny) || raw.deny.some(n => typeof n !== 'string')) throw new HttpError(400, 'invalid_request', 'policy.deny 必须是字符串数组')
    policy.deny = [...raw.deny]
  }
  if (raw.gate !== undefined) {
    const { requiredTool, maxNudges } = raw.gate ?? {}
    if (requiredTool !== undefined) requireString(requiredTool, 'policy.gate.requiredTool')
    if (maxNudges !== undefined && !(Number.isInteger(maxNudges) && maxNudges >= 0 && maxNudges <= 5)) {
      throw new HttpError(400, 'invalid_request', 'policy.gate.maxNudges 必须是 0 到 5 的整数')
    }
    policy.gate = { ...(requiredTool === undefined ? {} : { requiredTool }), ...(maxNudges === undefined ? {} : { maxNudges }) }
  }
  return policy
}

/** 回调令牌：工具桥（受众 tools）与技能提供方（受众 skills）各一个，业务服务签发。宿主只转交，不解读。 */
function accessOf(raw, field) {
  if (raw === undefined) return undefined
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) throw new HttpError(400, 'invalid_request', `${field} 必须是对象`)
  return { token: toolTokenOf(raw.token, `${field}.token`) }
}

function toolTokenOf(value, field) {
  requireString(value, field)
  if (value.length < 32 || value.length > 4096) throw new HttpError(400, 'invalid_request', `${field} 长度不合法`)
  return value
}

/**
 * @param {import('@deepseek-ai/cordis').Context} ctx
 * @param {{ host?: string, port?: number, token?: string, provider?: string, model?: string, modelTokenRef?: string }} [config]
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

  /** @type {Map<string, import('@deepseek-ai/dsh-agent').AgentHandle>} */
  const handles = new Map()
  /** @type {Map<string, Promise<unknown>>} */
  const opening = new Map()
  const closing = new Set()
  let shuttingDown = false

  ctx.on('session/event', (session, event) => {
    publish(String(session.id), 'session/event', event)
  })
  ctx.on('agent/status', ({ agent, status }) => {
    publish(String(agent.session.id), 'agent/status', { status })
  })
  // 逐字输出：官方 dsh-agent-loop 的 agent/assistant-stream（官方会话接口 dsh-api-session-controller、
  // 命令行运行 dsh-headless 都订阅它出字）。只转正文片段，作为临时帧推给当前订阅方；
  // 这一步的正式消息照常落成会话事件，业务服务拿它整段替换。子 Agent 的会话不是宿主打开的，查不到就丢。
  ctx.on('agent/assistant-stream', ({ agent, frame }) => {
    const data = liveFrame(frame)
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
    const sessionId = sessionIdOf(requireString(body.sessionId, 'sessionId'))
    const resume = body.resume === true
    if (resume && body.policy === undefined) {
      throw new HttpError(400, 'policy_required', '恢复会话必须重新给出 policy，宿主不保存策略')
    }
    if (handles.has(sessionId) || opening.has(sessionId) || closing.has(sessionId) || getSession(sessionId) !== undefined) {
      throw new HttpError(409, 'session_busy', `会话已打开、正在打开或正在释放：${sessionId}`)
    }
    const policy = policyOf(body.policy)
    const toolAccess = accessOf(body.tools, 'tools')
    const skillAccess = accessOf(body.skills, 'skills')
    const state = createSession(sessionId, policy)
    state.toolAccess = toolAccess
    state.skillAccess = skillAccess
    const id = brandString(sessionId)
    const task = resume
      ? ctx.agents.resume({ resumeSessionId: id, agentOptions })
      : ctx.agents.create({ sessionId: id, meta: { cwd: sessionRoot(sessionId) }, agentOptions })
    opening.set(sessionId, task)
    try {
      const handle = await task
      handles.set(sessionId, handle)
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
    const agent = liveAgent(sessionId)
    const text = requireString(body.text, 'text')
    const mode = body.mode ?? 'followup'
    if (mode !== 'followup' && mode !== 'steer') throw new HttpError(400, 'invalid_request', 'mode 只能是 followup 或 steer')
    const language = body.language
    if (language !== undefined && language !== 'zh' && language !== 'en') {
      throw new HttpError(400, 'invalid_request', 'language 只能是 zh 或 en')
    }
    const answerMode = body.answerMode ?? 'agent'
    if (answerMode !== 'agent' && answerMode !== 'quick') {
      throw new HttpError(400, 'invalid_request', 'answerMode 只能是 agent 或 quick')
    }
    let files
    let knowledge
    let mentions
    try {
      files = attachmentsOf(body.attachments)
      knowledge = catalogOf(body.knowledge)
      mentions = mentionsOf(body.mentions)
    } catch (error) {
      throw new HttpError(400, 'invalid_request', error.message)
    }
    const state = getSession(sessionId)
    if (state !== undefined && language !== undefined) state.language = language
    if (state !== undefined && knowledge !== undefined) state.knowledgeCatalog = knowledge
    const content = [{ type: 'text', text }, ...attachmentBlocks(files), ...mentionBlocks(mentions)]
    const message = createUserMessage({ content, source: { kind: 'user' } })
    // 回答方式等这条消息进入步骤时才生效（answer-mode）：排队到下一轮的消息不改正在跑的这一轮
    state?.answerModes.set(String(message.id), answerMode)
    if (mode === 'steer') agent.steer(message)
    else agent.followup(message)
    return { messageId: message.id, mode }
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
      await handle.dispose()
      return { disposed: true }
    } finally {
      closing.delete(sessionId)
    }
  }

  function streamEvents(sessionId, url, req, res) {
    const state = getSession(sessionId)
    if (state === undefined || closing.has(sessionId)) throw new HttpError(404, 'session_not_found', `会话不存在：${sessionId}`)
    const epoch = url.searchParams.get('epoch')
    if (epoch !== null && epoch !== EPOCH) throw new HttpError(410, 'epoch_mismatch', '宿主已重启，游标失效，请从头读取')
    if (state.subscribers.size >= MAX_SUBSCRIBERS_PER_SESSION) throw new HttpError(429, 'too_many_subscribers', '订阅过多')
    const afterRaw = Number(url.searchParams.get('after') ?? 0)
    const after = Number.isSafeInteger(afterRaw) && afterRaw >= 0 ? afterRaw : 0

    res.writeHead(200, { 'content-type': 'application/x-ndjson; charset=utf-8', 'cache-control': 'no-store' })
    const line = frame => res.write(`${JSON.stringify(frame)}\n`)
    const oldest = oldestCursor(state)
    if (after > 0 && oldest !== undefined && after < oldest - 1) {
      line({ epoch: EPOCH, cursor: oldest - 1, kind: 'gap', time: Date.now(), data: { after, oldest } })
    }
    for (const frame of framesAfter(state, after)) line(frame)

    let ended = false
    const subscriber = {
      write: frame => !ended && line(frame),
      end: () => {
        if (ended) return
        ended = true
        clearInterval(heartbeat)
        state.subscribers.delete(subscriber)
        res.end()
      },
    }
    const heartbeat = setInterval(() => { if (!res.write('\n')) subscriber.end() }, HEARTBEAT_MS)
    state.subscribers.add(subscriber)
    req.on('close', () => subscriber.end())
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

  function listApprovals(sessionId) {
    const state = getSession(sessionId)
    if (state === undefined) throw new HttpError(404, 'session_not_found', `会话不存在：${sessionId}`)
    return [...state.approvals.values()].map(({ id, sessionId: from, toolName, callId, reason }) => ({ id, sessionId: from, toolName, callId, reason }))
  }

  function decide(sessionId, approvalId, body) {
    const pending = getSession(sessionId)?.approvals.get(approvalId)
    if (pending === undefined) throw new HttpError(404, 'approval_not_found', `审批不存在或已结束：${approvalId}`)
    const outcome = body.outcome
    if (outcome !== 'allowed-once' && outcome !== 'rejected') {
      throw new HttpError(400, 'invalid_request', 'outcome 只能是 allowed-once 或 rejected')
    }
    pending.resolve(outcome)
    return { approvalId, outcome }
  }

  /** 官方 userQuestions 投影里本会话还能回答、但已不在前台等待的问题（等待到期、或宿主重启前没答完）。 */
  function continuedQuestions(agent) {
    const view = ctx.get('sessionProjections')?.stateOf(agent.session, 'userQuestions')
    return (view?.questions.active ?? []).filter(question => question.state === 'continued')
  }

  /** 待答问题：前台等待中的（open）在前，已转入 continued 的在后。会话没在运行时只看得到前者。 */
  function listQuestions(sessionId) {
    const state = getSession(sessionId)
    if (state === undefined) throw new HttpError(404, 'session_not_found', `会话不存在：${sessionId}`)
    const open = [...state.questions.values()].map(({ id, sessionId: from, questions, timed }) => ({ id, sessionId: from, questions, timed, state: 'open' }))
    const handle = handles.get(sessionId)
    const continued = handle === undefined ? [] : continuedQuestions(handle.agent)
      .filter(question => !state.questions.has(String(question.callId)))
      .map(question => ({ id: String(question.callId), sessionId, questions: question.questions, timed: true, state: 'continued' }))
    return [...open, ...continued]
  }

  /**
   * 提交回答。前台等待中的问题直接交回；已转入 continued 的经官方 `userQuestions.answer` 以
   * user-question-reply 消息送回 agent（空闲时会开新一轮）。
   */
  function answerQuestion(sessionId, questionId, body) {
    const answer = validated(() => parseAnswer(body))
    const pending = getSession(sessionId)?.questions.get(questionId)
    if (pending !== undefined) {
      validated(() => checkAnswer(pending.questions, answer))
      pending.answer(answer)
      return { questionId, state: 'answered' }
    }
    const agent = liveAgent(sessionId)
    const service = ctx.get('userQuestions')
    const question = continuedQuestions(agent).find(item => String(item.callId) === questionId)
    if (service === undefined || question === undefined) {
      throw new HttpError(404, 'question_not_found', `问题不存在或已结束：${questionId}`)
    }
    validated(() => checkAnswer(question.questions, answer))
    let accepted
    try {
      accepted = service.answer(agent, question.callId, answer)
    } catch (error) {
      if (error?.code === 'REPLY_QUEUED') throw new HttpError(409, 'reply_queued', '这个问题的回答已在排队，等 Agent 接收')
      if (error?.code === 'BAD_ANSWER') throw new HttpError(400, 'invalid_answer', '每个问题都要有一条回答')
      throw error
    }
    if (!accepted) throw new HttpError(404, 'question_not_found', `问题不存在或已结束：${questionId}`)
    return { questionId, state: 'queued' }
  }

  async function route(req, res) {
    const url = new URL(req.url ?? '/', 'http://host')
    const path = url.pathname
    if (req.method === 'GET' && path === '/health') return send(res, 200, { ok: !shuttingDown, epoch: EPOCH, dsh: DSH_VERSION })
    if (!tokenMatches(tokenDigest, req.headers.authorization)) throw new HttpError(401, 'unauthorized', '令牌无效')

    if (req.method === 'POST' && path === '/v1/sessions') return send(res, 201, await openSession(await readJson(req)))
    if (req.method === 'PUT' && path === '/v1/model-credential') return send(res, 200, await rotateModelCredential(await readJson(req)))

    const match = /^\/v1\/sessions\/([^/]+)(?:\/([^/]+)(?:\/([^/]+))?)?$/.exec(path)
    if (match === null) throw new HttpError(404, 'not_found', '路径不存在')
    const sessionId = sessionIdOf(match[1])
    const action = match[2]
    const sub = match[3]

    if (req.method === 'DELETE' && action === undefined) return send(res, 200, await dispose(sessionId))
    if (req.method === 'GET' && action === undefined) return send(res, 200, { sessionId, status: liveAgent(sessionId).status })
    if (req.method === 'POST' && action === 'messages') return send(res, 202, sendMessage(sessionId, await readJson(req)))
    if (req.method === 'POST' && action === 'cancel') return send(res, 200, await cancel(sessionId))
    if (req.method === 'PUT' && action === 'tool-credential') return send(res, 200, rotateAccess(sessionId, await readJson(req), 'toolAccess'))
    if (req.method === 'PUT' && action === 'skill-credential') return send(res, 200, rotateAccess(sessionId, await readJson(req), 'skillAccess'))
    if (req.method === 'GET' && action === 'events') return streamEvents(sessionId, url, req, res)
    if (req.method === 'GET' && action === 'approvals' && sub === undefined) return send(res, 200, listApprovals(sessionId))
    if (req.method === 'POST' && action === 'approvals' && sub !== undefined) {
      return send(res, 200, decide(sessionId, segmentOf(sub), await readJson(req)))
    }
    if (req.method === 'GET' && action === 'questions' && sub === undefined) return send(res, 200, listQuestions(sessionId))
    if (req.method === 'POST' && action === 'questions' && sub !== undefined) {
      return send(res, 200, answerQuestion(sessionId, segmentOf(sub), await readJson(req)))
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

/**
 * 官方逐字帧 → 转给业务服务的样子：开始（带轮次、步）、正文片段、结束。思考与工具参数的片段不转。
 * @param {{ type: string, attemptId?: string, turn?: number, step?: number, chunk?: { type: string, text?: string } }} frame
 */
function liveFrame(frame) {
  if (frame.type === 'start') return { type: 'start', attemptId: frame.attemptId, turn: frame.turn, step: frame.step }
  if (frame.type === 'end') return { type: 'end', attemptId: frame.attemptId }
  if (frame.type === 'chunk' && frame.chunk?.type === 'text-delta' && typeof frame.chunk.text === 'string') {
    return { type: 'text', attemptId: frame.attemptId, text: frame.chunk.text }
  }
  return undefined
}
