/**
 * 审批应答者：官方 `dsh-user-approval` 先在发起审批的会话日志里记下 `approval/asked`（带官方编号），再把待答的提问经瀑布事件
 * `approval/request` 交给应答者（只带工具名、callId、原因，不带编号），答完记 `approval/decided`。本插件是最终应答者，
 * 决定来自宿主 HTTP 接口（业务服务代用户提交）。
 *
 * 审批只认官方日志一个来源：监听会话事件记下 approval/asked，收到提问时按 callId
 * （没有 callId 时按工具名，取最近那条）认出官方编号，待答项就按官方编号记；不另发审批帧。页面上的审批卡片（投影取自日志）、
 * 业务服务的待审批列表与批准接口用的都是这一个编号，本机会话（桌面端答官方的提问）也是，两边走同一条路。
 *
 * 官方约定：应答者是 waterfall 监听者，返回结果即作答；没有应答者时请求按失败关闭。
 * 请求被中止、会话被释放时结算为 cancelled；超过等待上限按 rejected 处理。
 * 不是经宿主打开的会话（没有会话状态）、认不出官方编号的，一律按 unavailable 失败关闭。
 * 子 Agent 的审批记在它自己的会话日志里（转发成根会话的 subagent/event），待答项按会话头 parentSession 归到根会话，
 * 由同一个外部接口决定；记录里的 sessionId 是实际发起审批的子会话。
 *
 * 「本会话内同类操作不再询问」：官方只有一次性批准（allowed-once），没有记住授权的机制。授权记在业务服务里（谁、哪个对话、
 * 哪个工具、哪一版运行配置），这里放行，分两处：
 * - 主路径在发起审批之前：照官方 auto-review 在 `tools/pre-execute` 里放行的做法（prepend，排在最外层，看得到工具桥和官方工具
 *   给的「要问」），有授权就直接放行。官方不会发起这一问，日志里没有记录，页面不出卡片、这一轮不进等待——不然自动批准也会
 *   短暂进入等待，IM 机器人看到等待就收尾说「要人工确认」、工作流的智能体步骤看到等待就判失败；
 * - 官方工具自己发起的审批（命令要跳出沙箱这类，工具层直接调审批服务）pre-execute 拦不到：这里在等用户之前再问一次，有授权
 *   就直接答 allowed-once，日志里照样一问一答，另发一个只带官方编号的标记 approval/auto，卡片据此写「自动批准」（不是第二份
 *   审批记录，也不带第二个编号）。pre-execute 已经查过的调用不再查。
 * 查询失败、超时一律当作没有授权，照常问人（失败关闭的方向是多问，不是放行）。
 */

import { setImmediate as nextTurn } from 'node:timers/promises'
import { createBridgeClient } from './tool-bridge-client.mjs'
import { publish, resolveRootSession } from './state.mjs'

export const name = 'knowwork-approval-broker'
export const inject = ['approval']

const MAX_TIMEOUT_MS = 24 * 60 * 60 * 1000
/** 每个会话最多记这么多条还没答完的 approval/asked（答完即删，这只是兜底） */
const MAX_ASKED = 64
/** pre-execute 查过授权（没有）的调用记这么多条，审批时据此不再查第二遍 */
const MAX_CHECKED = 256
const ALLOW = Object.freeze({ kind: 'allow' })

/** @param {import('@deepseek-ai/cordis').Context} ctx @param {{ timeoutMs?: number, toolsBaseUrl?: string }} [config] */
export function apply(ctx, config) {
  const timeoutMs = config?.timeoutMs ?? 30 * 60 * 1000
  if (!Number.isInteger(timeoutMs) || timeoutMs <= 0 || timeoutMs > MAX_TIMEOUT_MS) {
    throw new Error(`knowwork-approval-broker: timeoutMs 必须是 1 到 ${MAX_TIMEOUT_MS} 之间的整数毫秒，当前为 ${timeoutMs}`)
  }
  const baseUrl = config?.toolsBaseUrl ?? process.env.KNOWWORK_TOOLS_BASE_URL ?? 'http://127.0.0.1:8001/internal/extensions/tools'
  const bridge = createBridgeClient({ baseUrl, executeTimeoutMs: timeoutMs })

  /** 会话 id → 还没答完的 approval/asked（按出现顺序） */
  const asked = new Map()
  /** pre-execute 已经查过授权、结论是没有的调用（callId） */
  const checked = new Set()

  ctx.on('session/event', (session, event) => {
    const sessionId = String(session.id)
    const data = event?.data
    if (event?.type === 'approval/asked' && typeof data?.id === 'string') {
      asked.set(sessionId, [...(asked.get(sessionId) ?? []), data].slice(-MAX_ASKED))
    } else if (event?.type === 'approval/decided' && asked.has(sessionId)) {
      const rest = asked.get(sessionId).filter(item => item.id !== data?.id)
      if (rest.length === 0) asked.delete(sessionId)
      else asked.set(sessionId, rest)
    }
  })

  /** 这次提问在日志里的官方编号：同一会话里最近一条工具名相同、callId 也对得上（提问带了 callId 时）的 approval/asked。 */
  function officialId(sessionId, req) {
    const found = [...(asked.get(sessionId) ?? [])].reverse()
      .find(item => item.toolName === req.toolName && (req.callId === undefined || item.callId === req.callId))
    return found?.id
  }

  /** 本会话是否已有这个工具的「不再询问」授权；没有令牌、出错一律 false。 */
  async function granted(state, req) {
    const token = state.toolAccess?.token
    if (token === undefined || typeof req.toolName !== 'string') return false
    try {
      return await bridge.grantCovers(token, { toolName: req.toolName, signal: req.signal })
    } catch (error) {
      ctx.logger.warn('approval grant lookup failed, asking the user: %s', error?.message)
      return false
    }
  }

  ctx.on('tools/pre-execute', async (exec, next) => {
    const decision = await next()
    if (decision.kind !== 'ask' || exec.agent === undefined) return decision
    const state = resolveRootSession(exec.agent.session)
    if (state === undefined) return decision
    if (await granted(state, { toolName: exec.name, signal: exec.signal })) return ALLOW
    if (typeof exec.callId === 'string') {
      checked.add(exec.callId)
      if (checked.size > MAX_CHECKED) checked.delete(checked.values().next().value)
    }
    return decision
  }, { prepend: true })

  ctx.on('approval/request', async (req) => {
    const sessionId = String(req.agent.session.id)
    const state = resolveRootSession(req.agent.session)
    if (state === undefined) return 'unavailable'
    if (req.signal?.aborted) return 'cancelled'
    // 官方先记日志再派发提问；日志事件要是晚一拍才到，等一轮事件循环再找
    const id = officialId(sessionId, req) ?? await nextTurn().then(() => officialId(sessionId, req))
    if (id === undefined) {
      ctx.logger.warn('approval request without a matching approval/asked in session %s, failing closed', sessionId)
      return 'unavailable'
    }
    const seen = typeof req.callId === 'string' && checked.delete(req.callId)
    if (!seen && await granted(state, req)) {
      publish(state.id, 'approval/auto', { id, sessionId })
      return 'allowed-once'
    }
    if (req.signal?.aborted) return 'cancelled'
    return new Promise((resolve) => {
      let settled = false
      const finish = (value) => {
        if (settled) return
        settled = true
        clearTimeout(timer)
        req.signal?.removeEventListener('abort', onAbort)
        state.approvals.delete(id)
        resolve(value)
      }
      const onAbort = () => finish('cancelled')
      const timer = setTimeout(() => finish('rejected'), timeoutMs)
      req.signal?.addEventListener('abort', onAbort, { once: true })
      state.approvals.set(id, { id, sessionId, toolName: req.toolName, callId: req.callId, reason: req.reason, resolve: finish })
    })
  })
}
