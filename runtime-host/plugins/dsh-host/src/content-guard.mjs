/**
 * 内容检查（敏感词）：员工发给模型的内容、模型的回答和它要调用的工具参数，命中管理员配的敏感词就拦下。
 * 词表与判断都在业务服务（content-safety-client.mjs 回调），这里只把文字送去、按结果放行或结束；空间没开就原样放行。
 *
 * 三处都用 DSH 官方的接缝，不改官方循环发出的请求（官方请求是冻结的，内容必须能从会话日志重放出来）：
 * - 员工的消息（以及长任务目标自动续跑发起的那一轮：目标是人用 /goal 写的，每一轮都交给模型）：官方 `agent/pre-step`，
 *   这一步认领的消息还没写进会话历史。命中就拒绝这一步（官方 reject，这一轮以
 *   blocked 结束），这几条消息不进历史，模型看不到，后面几轮也不会再带上它、对话不会卡住。结束原因里没有话术，
 *   所以先往会话日志写一条 `content-safety/blocked`，带上阻断提示和被拒的消息（业务服务照普通提问展示）。写法照官方
 *   dsh-llm-retry 的 `llm/retry`：持久、不进模型看到的历史；服务端宿主经事件流、桌面端的本机宿主经上传，都原样到业务服务；
 * - 工具与附件取回的内容：官方 `tools/post-execute`，结果写进历史之前。命中就用官方 block 把结果换成一句说明（敏感内容
 *   不进历史），再在这段会话下一次调模型时直接结束这一轮；
 * - 模型的回答与工具参数：官方 `llm/stream`，包住输出流边放边查（content-guard-stream.mjs）。
 * 检查接口不通时开了检查的不放行，以 CHECK_UNAVAILABLE 结束（不可用不等于通过）。
 * 个人专属宿主里，模型网关还会按同一份策略再查一遍（这里跑着没审过的上传插件，插件可能把这个检查绕开）；网关拦下时
 * 官方适配器报调用失败，这里认出来换成同样的结束（content-guard-stream.mjs 的 gatewayEndings）。
 * 「检查输入」管前两处，「检查输出」管第三处。子 Agent 按根会话的令牌与策略查，被拦下只结束子 Agent 自己的这一轮。
 * 检查接口的地址要显式给（KNOWWORK_CONTENT_SAFETY_BASE_URL：服务端是内部端口，桌面端是公开入口），没给就不查：
 * 不退回默认地址，免得在用户电脑上把会话令牌发给本机碰巧占着那个端口的程序。
 */

import { CheckUnavailable, createContentSafetyClient } from './content-safety-client.mjs'
import { finishWith, gatewayEndings, guardOutput } from './content-guard-stream.mjs'
import { getSession, onSessionDropped, rootSessionIdOf } from './state.mjs'

export const name = 'knowwork-content-guard'

export const BLOCKED_EVENT = 'content-safety/blocked'
const UNAVAILABLE = '内容检查暂时不可用，请稍后再试'
const TOOL_BLOCKED = '这个工具返回的内容命中了企业敏感词，已被拦下，不能使用。'
const TOOL_UNAVAILABLE = '内容检查暂时不可用，这个工具返回的内容没能检查，已被拦下。'
const MAX_TRACKED = 10_000

function textOf(content) {
  if (!Array.isArray(content)) return ''
  return content.filter(block => block?.type === 'text' && typeof block.text === 'string').map(block => block.text).join('\n')
}

function remember(map, key, value) {
  if (map.size >= MAX_TRACKED && !map.has(key)) map.delete(map.keys().next().value)
  map.set(key, value)
}

/** @param {import('@deepseek-ai/cordis').Context} ctx @param {{ baseUrl?: string }} [config] */
export function apply(ctx, config) {
  const client = createContentSafetyClient({ baseUrl: config?.baseUrl })
  if (!client.configured) ctx.logger.info('no content safety entry configured, content checks are off')
  /** 会话 → 它正在走的那一轮（pre-step 记下）；工具结果被拦下时按它判断「下一次调模型」还在不在同一轮 */
  const turns = new Map()
  /** 会话 → 工具结果被拦下、等下一次调模型时结束这一轮 */
  const pending = new Map()

  ctx.effect(() => onSessionDropped(sessionId => {
    client.forget(sessionId)
    turns.delete(sessionId)
    pending.delete(sessionId)
  }))

  /** 这段会话归谁查：根会话的令牌与策略。没有令牌（不是经业务服务打开的会话）就不查 */
  async function policyFor(sessionId) {
    const root = rootSessionIdOf(sessionId)
    const token = root === undefined ? undefined : getSession(root)?.toolAccess?.token
    if (token === undefined) return undefined
    const policy = await client.policy(root, token)
    return { root, token, policy }
  }

  /** 送去查；干净返回 undefined，命中或查不成返回结束原因。令牌取根会话这会儿的：业务服务每隔有效期的三分之一换一次，
   *  一次模型调用可能比有效期（15 分钟）还长（推理模型、本地部署出字慢，模型网关一次调用的上限默认 20 分钟），调用开头
   *  记下的那个用到后面已经过期，「令牌无效」会把整轮当成检查不可用拦下（2026-11-25 实测：推理 18 分钟后才开始写的一次调用） */
  async function verify(access, direction, texts, tool, signal) {
    const token = getSession(access.root)?.toolAccess?.token ?? access.token
    try {
      const verdict = await client.check(token, { direction, tool, texts, holdback: access.policy.holdback, signal })
      return verdict.blocked ? { code: 'CONTENT_BLOCKED', message: verdict.message } : undefined
    } catch (error) {
      if (signal?.aborted || !(error instanceof CheckUnavailable)) throw error
      ctx.logger.warn('content safety check unavailable, blocking: %s', error.message)
      return { code: 'CHECK_UNAVAILABLE', message: UNAVAILABLE }
    }
  }

  /** 长任务目标（官方 dsh-goal，开了这个插件才有）还在进行中就标成卡住 */
  function blockGoal(agent, message) {
    const goals = ctx.get('goals')
    const goal = goals?.get(agent)
    if (goal === undefined || goal.phase !== 'active') return
    goals.block(agent, { id: goal.id, revision: goal.revision }, { code: 'content-blocked', message })
  }

  ctx.on('agent/pre-step', async (payload, next) => {
    const decision = await next()
    const sessionId = String(payload.agent.session.id)
    remember(turns, sessionId, payload.turn)
    if (decision.kind !== 'enter') return decision
    const asked = decision.messages.filter(message => message.source?.kind === 'user')
    const rounds = decision.messages.filter(message => message.source?.kind === 'goal')
    if (asked.length === 0 && rounds.length === 0) return decision
    const access = await policyFor(sessionId)
    if (access === undefined || !access.policy.input) return decision
    const texts = [...asked, ...rounds].map(message => textOf(message.content))
    const ending = await verify(access, 'input', texts, undefined, payload.signal)
    if (ending === undefined) return decision
    if (sessionId === access.root && asked.length > 0) {
      payload.agent.session.append(BLOCKED_EVENT, {
        turn: payload.turn, code: ending.code, message: ending.message,
        messages: asked.map(message => ({ id: message.id, content: message.content })),
      })
    }
    // 续跑那一轮被拦：把目标标成卡住、写明原因（官方续跑插件看到目标不在进行中就停下，不会反复发起）
    if (rounds.length > 0) blockGoal(payload.agent, ending.message)
    return { kind: 'reject' }
  })

  ctx.on('tools/post-execute', async (exec, result, next) => {
    const decision = await next()
    if (decision.kind !== 'accept' || exec.agent === undefined) return decision
    const sessionId = String(exec.agent.session.id)
    const access = await policyFor(sessionId)
    if (access === undefined || !access.policy.input) return decision
    const value = decision.value ?? result.value
    const texts = [textOf(decision.content ?? result.content), value === undefined ? '' : JSON.stringify(value)]
    const ending = await verify(access, 'input', texts, exec.name, exec.signal)
    if (ending === undefined) return decision
    remember(pending, sessionId, { ...ending, turn: turns.get(sessionId) })
    const text = ending.code === 'CHECK_UNAVAILABLE' ? TOOL_UNAVAILABLE : TOOL_BLOCKED
    return { kind: 'block', feedback: [{ type: 'text', text }], info: { name: 'ContentBlocked', code: ending.code } }
  })

  ctx.on('llm/stream', (options, next) => guarded(options, next))

  async function* guarded(options, next) {
    const sessionId = options.sessionId === undefined ? undefined : String(options.sessionId)
    const held = sessionId === undefined ? undefined : pending.get(sessionId)
    if (held !== undefined) {
      pending.delete(sessionId)
      if (held.turn === turns.get(sessionId)) {
        // 工具结果被拦下以后的第一次调模型：不调了，这一轮就此结束
        yield finishWith(held)
        return
      }
    }
    const access = sessionId === undefined ? undefined : await policyFor(sessionId)
    if (access === undefined || !access.policy.output) {
      yield* gatewayEndings(next())
      return
    }
    yield* guardOutput(gatewayEndings(next()), {
      holdback: access.policy.holdback,
      verify: (direction, texts, tool) => verify(access, direction, texts, tool, options.signal),
    })
  }
}
