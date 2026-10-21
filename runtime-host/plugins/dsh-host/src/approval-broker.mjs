/**
 * 审批应答者：把官方 `dsh-user-approval` 的 `approval/request` 转给业务服务，等外部决定。
 *
 * 官方约定：应答者是 waterfall 监听者，返回结果即作答；没有应答者时请求按失败关闭。
 * 本插件是最终应答者，决定来自宿主 HTTP 接口（业务服务代用户提交）。
 * 请求被中止、会话被释放时结算为 cancelled；超过等待上限按 rejected 处理。
 * 不是经宿主打开的会话（没有会话状态）一律按 unavailable 失败关闭。
 * 子 Agent 的审批按会话头 parentSession 归到根会话：出现在根会话的审批列表与事件流里，
 * 由同一个外部接口决定；记录里的 sessionId 是实际发起审批的子会话。
 *
 * 「本会话内同类操作不再询问」：官方只有一次性批准（allowed-once），没有记住授权的
 * 机制，但应答者怎么作答由组合方决定。授权记在业务服务里（谁、哪个对话、哪个工具、哪一版运行配置），
 * 这里在弹给用户之前先问一次：有授权就直接答 allowed-once 并发一条 approval/granted 留痕，不再弹卡片；
 * 查询失败、超时一律当作没有授权，照常问人（失败关闭的方向是多问，不是放行）。
 */

import { randomUUID } from 'node:crypto'
import { createBridgeClient } from './tool-bridge-client.mjs'
import { publish, resolveRootSession } from './state.mjs'

export const name = 'knowwork-approval-broker'
export const inject = ['approval']

const MAX_TIMEOUT_MS = 24 * 60 * 60 * 1000

/** @param {import('@deepseek-ai/cordis').Context} ctx @param {{ timeoutMs?: number, toolsBaseUrl?: string }} [config] */
export function apply(ctx, config) {
  const timeoutMs = config?.timeoutMs ?? 30 * 60 * 1000
  if (!Number.isInteger(timeoutMs) || timeoutMs <= 0 || timeoutMs > MAX_TIMEOUT_MS) {
    throw new Error(`knowwork-approval-broker: timeoutMs 必须是 1 到 ${MAX_TIMEOUT_MS} 之间的整数毫秒，当前为 ${timeoutMs}`)
  }
  const baseUrl = config?.toolsBaseUrl ?? process.env.KNOWWORK_TOOLS_BASE_URL ?? 'http://127.0.0.1:8001/internal/extensions/tools'
  const bridge = createBridgeClient({ baseUrl, executeTimeoutMs: timeoutMs })

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

  ctx.on('approval/request', async (req) => {
    const sessionId = String(req.agent.session.id)
    const state = resolveRootSession(req.agent.session)
    if (state === undefined) return 'unavailable'
    const rootId = state.id
    if (req.signal?.aborted) return 'cancelled'
    if (await granted(state, req)) {
      publish(rootId, 'approval/granted', { sessionId, toolName: req.toolName, callId: req.callId })
      return 'allowed-once'
    }
    if (req.signal?.aborted) return 'cancelled'
    const id = randomUUID()
    const outcome = await new Promise((resolve) => {
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
      publish(rootId, 'approval/requested', { id, sessionId, toolName: req.toolName, callId: req.callId, reason: req.reason })
    })
    publish(rootId, 'approval/resolved', { id, sessionId, outcome })
    return outcome
  })
}
