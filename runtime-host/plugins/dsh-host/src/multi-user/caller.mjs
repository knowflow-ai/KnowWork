/**
 * 多用户适配层（一）：工具执行时的调用者。
 *
 * 官方插件从不问「是谁在调」：DSH 一个宿主只有一个属主。KnowWork 一个宿主服务所有空间、所有人，属主级插件得知道
 * 这次是哪个空间的哪个人。官方执行工具时有一个包住整个执行过程的 waterfall `tools/execute`，这里在它里面用
 * AsyncLocalStorage 设上调用者，工具里调到的任何官方服务（例如定时任务的 list）都能用 currentCaller() 取到。
 *
 * 调用者 = 根会话（子 Agent 归到根会话）+ 它的属主（业务服务打开会话时给，state.owner）。属主不明（旧版业务服务、
 * 不是宿主打开的会话）时不设，属主级适配按「没有调用者」处理。由属主服务（owners.mjs）装上。
 */

import { AsyncLocalStorage } from 'node:async_hooks'
import { resolveRootSession } from '../state.mjs'

/** @typedef {{ workspaceId: string, userId: string }} Owner */
/** @typedef {{ sessionId: string, owner: Owner }} Caller */

/** @type {AsyncLocalStorage<Caller>} */
const store = new AsyncLocalStorage()

/** @param {import('@deepseek-ai/cordis').Context} ctx */
export function trackCallers(ctx) {
  ctx.on('tools/execute', (exec, next) => {
    const caller = callerOf(exec.agent)
    return caller === undefined ? next() : store.run(caller, next)
  })
}

/** @returns {Caller | undefined} 正在执行的工具的调用者；不在工具执行里（宿主内部的定时器等）是 undefined */
export function currentCaller() {
  return store.getStore()
}

/** @returns {Caller | undefined} */
export function callerOf(agent) {
  const root = agent === undefined ? undefined : resolveRootSession(agent.session)
  if (root?.owner === undefined) return undefined
  return { sessionId: root.id, owner: root.owner }
}

/** 同一个空间的同一个人 */
export function sameOwner(a, b) {
  return a !== undefined && b !== undefined && a.workspaceId === b.workspaceId && a.userId === b.userId
}
