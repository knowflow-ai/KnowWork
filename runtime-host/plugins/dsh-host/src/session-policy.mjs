/**
 * 会话级工具策略：会话创建或恢复时，按业务服务下发的策略收窄本会话的工具（规则见 tool-deny）。
 * 全局工具用官方 `restrict`，业务工具由工具桥不注册，二者都再加官方 `guard`；未知名字不致命，由工具桥记日志。
 * 只作用于该 agent，不影响其他会话。
 *
 * 子 Agent 的会话 id 不同，查不到这里的策略；按会话头 parentSession 继承根会话策略的逻辑在 subagent-policy。
 * 实测官方作用域链不会把父 agent 的 restrict 带给子 agent，所以那边必须再施加一次。
 */

import { getSession } from './state.mjs'
import { applyDeny, denyOf } from './tool-deny.mjs'

export const name = 'knowwork-session-policy'
export const inject = ['agents', 'tools']

/** @param {import('@deepseek-ai/cordis').Context} ctx */
export function apply(ctx) {
  // 不带作用域的 get 是全局视图：只有全局层注册的名字能交给 restrict
  const isGlobal = name => ctx.tools.get(name) !== undefined
  ctx.on('agent/created', ({ agent }) => {
    applyDeny(agent, denyOf(getSession(String(agent.session.id))), isGlobal)
    return undefined
  })
}
