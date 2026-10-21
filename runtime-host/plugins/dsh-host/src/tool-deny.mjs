/**
 * 会话策略 deny 名单的施加规则，根会话（session-policy）与子 Agent（subagent-policy）共用。
 *
 * 官方 `tools.restrict` 只收窄**继承来的**工具（全局层与祖先层），遇到本作用域自己注册的名字或未知名字直接抛错
 * （`names unknown global tool`），会让会话打不开。业务工具由工具桥按 agent 作用域注册，正是这类名字。
 * 所以按官方语义分三类处理：
 * - 全局工具：交给 `restrict`（模型看不到，官方同时阻止执行）；
 * - 业务工具：工具桥不注册它（`isDenied`），这是官方对作用域工具唯一的「收窄」方式；
 * - 未知名字：忽略并由工具桥记一条宿主日志（`unknownDenied`），不让会话打不开。
 * 三类都再加一道 agent 级 `guard`：凭空调用也拒绝执行，且其他插件无法推翻。
 */

export const DENY_REASON = name => `工具 ${name} 不在本会话的授权范围内`

/** 策略里的 deny 名单（不合法时视为空）。 */
export function denyOf(state) {
  const deny = state?.policy?.deny
  return Array.isArray(deny) ? deny : []
}

/** 名字是否在会话策略的 deny 名单里。 */
export function isDenied(state, name) {
  return denyOf(state).includes(name)
}

/**
 * 给 agent 施加 deny：全局工具 restrict，全部名字 guard。
 * @param {object} agent
 * @param {readonly string[]} deny
 * @param {(name: string) => boolean} isGlobal 名字是否为全局层注册的工具（官方 restrict 只认这类）
 */
export function applyDeny(agent, deny, isGlobal) {
  if (deny.length === 0) return
  const globals = deny.filter(isGlobal)
  if (globals.length > 0) agent.ctx.tools.restrict({ deny: globals })
  const denied = new Set(deny)
  agent.ctx.tools.guard(exec => denied.has(exec.name) ? DENY_REASON(exec.name) : undefined)
}

/**
 * deny 名单里既不是全局工具、也不是本会话业务工具的名字。
 * @param {readonly string[]} deny
 * @param {(name: string) => boolean} isGlobal
 * @param {ReadonlySet<string>} businessNames
 */
export function unknownDenied(deny, isGlobal, businessNames) {
  return deny.filter(name => !isGlobal(name) && !businessNames.has(name))
}
