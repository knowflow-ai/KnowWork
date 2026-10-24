/**
 * 专业工具的解锁：被某个技能认领的专业工具，在模型加载该技能之前不可见。
 *
 * DSH 官方没有「加载技能后才露出工具」的机制（tool-skill 只出目录与加载工具，不看 allowed-tools），
 * 所以用官方的工具作用域能力自己组合，按 agent 记账：
 * - 业务工具（工具桥按 agent 作用域注册）：先不注册，登记成「待解锁」；解锁时才 `agent.ctx.tools.register`。
 *   官方 `restrict` 不能收窄作用域自己注册的工具，所以这类只能延后注册；
 * - 全局工具（宿主内置）：`agent.ctx.tools.restrict({ deny })`，解锁时调它返回的撤销函数；
 * - 两类都加一道 agent 级 `guard`：没解锁就调用一律拒绝，并告诉模型先加载哪个技能。guard 不能被别的插件推翻。
 * 解锁按 agent 生效：子 Agent 自己加载技能才解锁自己的工具。
 *
 * lockTools 幂等：工具桥与技能提供方在各自的 agent/created 里都会调用，谁先谁后都一样。
 */

/** @type {WeakMap<object, { owners: Map<string, string>, unlocked: Set<string>, deferred: Map<string, () => void>, lifts: Map<string, () => void> }>} */
const books = new WeakMap()

function bookOf(agent) {
  return books.get(agent)
}

/**
 * 给 agent 登记待解锁的工具，并装上 guard 与全局工具的收窄。幂等，第二次起直接返回。
 * 必须在工具桥注册业务工具之前或同一个 agent/created 回合里调用。
 * @param {object} agent
 * @param {Map<string, string>} owners 专业工具 → 认领它的技能名
 */
export function lockTools(agent, owners) {
  if (owners.size === 0 || books.has(agent)) return
  const book = { owners: new Map(owners), unlocked: new Set(), deferred: new Map(), lifts: new Map() }
  books.set(agent, book)
  const visible = new Set(agent.ctx.tools.schemas(agent).map(s => s.name))
  for (const name of owners.keys()) {
    // 只有当前可见的全局工具需要 restrict；业务工具此时还没注册，由工具桥走 defer
    if (visible.has(name)) book.lifts.set(name, agent.ctx.tools.restrict({ deny: [name] }))
  }
  agent.ctx.tools.guard(exec => {
    if (!book.owners.has(exec.name) || book.unlocked.has(exec.name)) return undefined
    return `工具 ${exec.name} 需要先加载技能 ${book.owners.get(exec.name)} 才能使用`
  })
}

/** 工具是否处于「待解锁」状态（工具桥据此决定先不注册）。 */
export function isLocked(agent, name) {
  const book = bookOf(agent)
  return book !== undefined && book.owners.has(name) && !book.unlocked.has(name)
}

/** 工具桥登记一个延后注册的业务工具；解锁时调用 register。 */
export function defer(agent, name, register) {
  bookOf(agent)?.deferred.set(name, register)
}

/**
 * 解锁一个技能认领的全部工具。返回本次新解锁的工具名（重复加载同一技能返回空数组）。
 * @param {object} agent
 * @param {string} skillName
 */
export function unlockSkill(agent, skillName) {
  const book = bookOf(agent)
  if (book === undefined) return []
  const opened = []
  for (const [name, owner] of book.owners) {
    if (owner !== skillName || book.unlocked.has(name)) continue
    book.unlocked.add(name)
    book.deferred.get(name)?.()
    book.deferred.delete(name)
    book.lifts.get(name)?.()
    book.lifts.delete(name)
    opened.push(name)
  }
  return opened
}
