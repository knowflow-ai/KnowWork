/**
 * 扩展补的每轮上下文（业务服务 conversations/turn_context.py，例如记忆）：业务服务随消息下发两段文字（context），
 * 由扩展写好、转义好，这里原样包进 <system-reminder>，作为用户角色消息追加，不动系统提示与工具声明。
 *
 * - standing（常驻）：照知识库目录（knowledge-catalog）的办法。每个模型步骤开始时和会话里最近一份比内容摘要：仍在可见历史里
 *   且没变就不发（不动前缀缓存）；变了追加一份并写明以这份为准；被压缩掉了重发；从有变成空时追加一句「不再适用」。
 * - turn：只和这一轮有关（例如按本轮提问召回的几条），紧跟在本轮提问后面追加一次，和提问一起进入那一步。
 * - 某一轮业务服务没算（不带 context）：常驻那段沿用上一轮的，这一轮也没有 turn 那段。
 * - 只给根会话：子 Agent 做的是分出去的具体任务，需要的背景由根会话交代。
 */

import { createHash } from 'node:crypto'
import { createUserMessage } from '@deepseek-ai/dsh-llm'
import { getSession } from './state.mjs'

export const name = 'knowwork-turn-context'

const KIND = 'knowwork-turn-context'
const CLEARED = 'cleared'
const MAX_STANDING = 8000
const MAX_TURN = 4000

/**
 * 校验业务服务下发的 context；没下发时返回 undefined。格式不对抛错，消息可以回给调用方。
 * @returns {{ standing: string, turn: string } | undefined}
 */
export function turnContextOf(value) {
  if (value === undefined || value === null) return undefined
  const { standing = '', turn = '' } = typeof value === 'object' ? value : {}
  if (typeof value !== 'object' || typeof standing !== 'string' || typeof turn !== 'string') {
    throw new Error('context 格式错误：要有 standing、turn 两段文字')
  }
  if (standing.length > MAX_STANDING || turn.length > MAX_TURN) throw new Error('context 过长')
  return Object.freeze({ standing, turn })
}

/**
 * 收下一条消息带来的 context：常驻那段换成这一份，turn 那段记在这条消息名下，等它进入步骤时追加。
 * @param {import('./state.mjs').SessionState | undefined} state
 */
export function receiveTurnContext(state, messageId, context) {
  if (state === undefined || context === undefined) return
  state.turnContext = context.standing
  if (context.turn !== '') state.turnTexts.set(String(messageId), context.turn)
}

const digestOf = text => createHash('sha256').update(text).digest('hex')
/** 常驻那段在来源里标 standing，和 turn 那段分开认 */
const reminder = (digest, lines, standing) => createUserMessage({
  content: [{ type: 'text', text: ['<system-reminder>', ...lines, '</system-reminder>'].join('\n') }],
  source: { kind: KIND, digest, ...(standing ? { standing: true } : {}) },
})

/** 会话里最近一份常驻那段：摘要、是否仍在可见历史里（压缩掉了要重发）。turn 那段不算（它没有摘要以外的意义）。 */
function latest(agent) {
  const visible = new Set(agent.session.surface.nodes)
  for (let index = agent.session.seq - 1; index >= 0; index -= 1) {
    const event = agent.session.eventAt(index)
    const source = event?.type === 'user/message' ? event.data.source : undefined
    if (source?.kind === KIND && source.standing === true) return { digest: source.digest, visible: visible.has(event.seq) }
  }
  return undefined
}

/** 这一步要不要追加常驻那段，要的话是哪一条。 */
function standingMessage(agent, text) {
  const past = latest(agent)
  if (text === '') {
    if (past === undefined || past.digest === CLEARED) return undefined
    return reminder(CLEARED, ['之前随对话提供的背景已经不再适用，不要再按它回答。'], true)
  }
  const digest = digestOf(text)
  if (past?.digest === digest && past.visible) return undefined
  const updated = past !== undefined && past.digest !== CLEARED
  return reminder(digest, updated ? ['以下内容更新了，取代之前同一部分的内容：', '', text] : [text], true)
}

/** @param {import('@deepseek-ai/cordis').Context} ctx */
export function apply(ctx) {
  ctx.on('agent/pre-step', async ({ agent }, next) => {
    const decision = await next()
    if (decision.kind === 'reject') return decision
    const state = getSession(String(agent.session.id))
    if (state === undefined) return decision
    // turn 那段紧跟在它那条提问后面；用过就删
    const messages = decision.messages.flatMap(m => {
      const text = state.turnTexts.get(String(m.id))
      if (text === undefined) return [m]
      state.turnTexts.delete(String(m.id))
      return [m, reminder(digestOf(text), [text], false)]
    })
    const standing = state.turnContext === undefined ? undefined : standingMessage(agent, state.turnContext)
    if (standing === undefined && messages.length === decision.messages.length) return decision
    return { ...decision, messages: standing === undefined ? messages : [...messages, standing] }
  })
}
