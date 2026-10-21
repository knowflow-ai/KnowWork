/**
 * 技能门（技术方案 6.3 节规则 3）：本轮结束前，完成判据必须满足，否则最终回答被拦下。
 *
 * 完成判据有两个来源，同时生效：
 * - 会话策略 `policy.gate.requiredTool`：本轮必须成功调用这个工具（E1 的形态，固定流程也用它）；
 * - 本轮根会话加载过的技能（skill-provider 记进 `state.gate.loaded`）：每个声明了 completionTools 的技能，
 *   其 completionTools 里至少一个在本轮成功调用过。
 *
 * 用官方 `agent/turn-stopping`：回合即将结束时检查；未满足就 `agent.steer()` 注入一条说明缺哪一步，
 * 让 Agent 再跑一步。补救次数有上限（policy.gate.maxNudges，默认 2），超过后放行结束并发出
 * skill-gate/failed 事件，由业务服务把这一轮标为失败并向用户说明。
 * 每轮开始（会话事件 `turn/start`）时清空本轮的成功、加载与补救记录，上一轮的不算数。
 * 子 Agent 不受技能门约束（门只管根会话的最终回答），但它成功调用的工具记到根会话本轮的成功记录里。
 */

import { createUserMessage } from '@deepseek-ai/dsh-llm'
import { getSession, publish, resolveRootSession } from './state.mjs'

export const name = 'knowwork-skill-gate'
export const inject = ['agents', 'tools']

const DEFAULT_MAX_NUDGES = 2

function resetTurn(state, turn) {
  state.gate.turn = turn
  state.gate.succeeded.clear()
  state.gate.loaded.clear()
  state.gate.nudges = 0
}

/** 还没满足的完成判据：[{ skill?, tools }]，tools 里任一成功即满足。 */
function missingOf(state) {
  const succeeded = state.gate.succeeded
  const missing = []
  const requiredTool = state.policy?.gate?.requiredTool
  if (requiredTool !== undefined && !succeeded.has(requiredTool)) missing.push({ tools: [requiredTool] })
  for (const [skill, tools] of state.gate.loaded) {
    if (tools.length > 0 && !tools.some(tool => succeeded.has(tool))) missing.push({ skill, tools: [...tools] })
  }
  return missing
}

function nudgeText(missing) {
  const lines = missing.map(({ skill, tools }) => {
    const options = tools.join('、')
    if (skill === undefined) return `- 先成功调用工具 ${options}`
    return `- 技能 ${skill} 还没完成：先成功调用 ${options}${tools.length > 1 ? ' 中的一个' : ''}`
  })
  return ['【完成判据】结束前还缺以下步骤，完成后再给出最终回答：', ...lines].join('\n')
}

function eventData(state, turn, missing) {
  const requiredTool = state.policy?.gate?.requiredTool
  return { turn, ...(requiredTool === undefined ? {} : { requiredTool }), skills: [...state.gate.loaded.keys()], missing }
}

/** @param {import('@deepseek-ai/cordis').Context} ctx */
export function apply(ctx) {
  ctx.on('session/event', (session, event) => {
    if (event.type !== 'turn/start') return
    const state = getSession(String(session.id))
    if (state !== undefined) resetTurn(state, event.data?.turn)
  })

  ctx.on('tools/result', (exec, result) => {
    if (exec.agent === undefined || result.isError !== false) return undefined
    resolveRootSession(exec.agent.session)?.gate.succeeded.add(exec.name)
    return undefined
  })

  ctx.on('agent/turn-stopping', ({ agent, turn }) => {
    const sessionId = String(agent.session.id)
    const state = getSession(sessionId)
    if (state === undefined) return
    if (state.gate.turn !== turn) {
      // 没收到本轮的 turn/start（不应发生）时按新一轮处理成功与补救记录，宁可多要求一次也不放过；
      // 本轮的加载记录保留，否则会把已加载技能的完成判据一起丢掉。
      state.gate.turn = turn
      state.gate.succeeded.clear()
      state.gate.nudges = 0
    }
    if (state.policy?.gate?.requiredTool === undefined && state.gate.loaded.size === 0) return
    const missing = missingOf(state)
    if (missing.length === 0) {
      publish(sessionId, 'skill-gate/passed', eventData(state, turn, missing))
      return
    }
    const maxNudges = state.policy?.gate?.maxNudges ?? DEFAULT_MAX_NUDGES
    if (state.gate.nudges >= maxNudges) {
      publish(sessionId, 'skill-gate/failed', { ...eventData(state, turn, missing), nudges: state.gate.nudges })
      return
    }
    state.gate.nudges += 1
    publish(sessionId, 'skill-gate/nudged', { ...eventData(state, turn, missing), nudge: state.gate.nudges })
    agent.steer(createUserMessage({
      content: [{ type: 'text', text: nudgeText(missing) }],
      source: { kind: 'knowwork-skill-gate' },
    }))
  })
}
