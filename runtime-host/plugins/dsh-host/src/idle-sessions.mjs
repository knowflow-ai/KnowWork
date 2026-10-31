/**
 * 空闲会话回收：宿主是多人共用的长驻进程，每个打开过的会话（历史消息、工具结果、作用域注册表）都留在内存里，
 * 宿主重启前只涨不降。空闲超过 KNOWWORK_HOST_SESSION_IDLE_MINUTES（默认 30 分钟，0 表示不回收）就释放；
 * 会话日志照常在磁盘上，业务服务下次发消息遇到「会话不在」走现成的恢复流程（同宿主重启后）。
 *
 * 只释放真正停下来的：agent 空闲、没有待决的审批与提问、没有事件流订阅者（业务服务的事件泵已退出）、
 * 名下没有还在跑的后台任务（含后台子 Agent）——官方释放会话会取消它名下的后台任务（dsh-jobs-local 的 disposeOwned）。
 * 业务服务定期续签令牌不算活动，否则会话永远不空闲。
 */

const DEFAULT_IDLE_MINUTES = 30
const MAX_SWEEP_MS = 60_000
const MIN_SWEEP_MS = 50
const LIVE_JOBS = new Set(['running', 'stopping'])

/** 空闲多久回收（毫秒）；0 表示不回收。允许小数分钟（测试用）。 */
export function idleMsOf(raw) {
  if (raw === undefined || raw === '') return DEFAULT_IDLE_MINUTES * 60_000
  const minutes = Number(raw)
  if (!Number.isFinite(minutes) || minutes < 0) {
    throw new Error(`KNOWWORK_HOST_SESSION_IDLE_MINUTES 必须是不小于 0 的数，当前为 ${raw}`)
  }
  return Math.round(minutes * 60_000)
}

/** 多久查一次：空闲时长的一半，夹在 50 毫秒到 1 分钟之间。 */
export const sweepMsOf = idleMs => Math.min(MAX_SWEEP_MS, Math.max(MIN_SWEEP_MS, Math.floor(idleMs / 2)))

/** 每个会话最近一次活动的时间。 */
export function createIdleTracker(idleMs, now = Date.now) {
  const last = new Map()
  return {
    touch(sessionId) { last.set(sessionId, now()) },
    forget(sessionId) { last.delete(sessionId) },
    due(sessionId) {
      const at = last.get(sessionId)
      return idleMs > 0 && at !== undefined && now() - at >= idleMs
    },
  }
}

/**
 * 能不能释放：agent 空闲、没有待决的审批与提问、没有订阅者、名下没有还在跑的后台任务。
 * 查后台任务出错时当作不能释放（宁可多占一会儿内存，也不误杀任务）。
 */
export function releasable(agent, state, jobs) {
  if (agent.status !== 'idle') return false
  if (state !== undefined && (state.approvals.size > 0 || state.questions.size > 0 || state.subscribers.size > 0)) {
    return false
  }
  if (jobs === undefined) return true
  try {
    return jobs.list(agent.id).every(job => !LIVE_JOBS.has(job.status))
  } catch {
    return false
  }
}
