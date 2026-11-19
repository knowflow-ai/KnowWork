/**
 * 远程执行器（remote-fs、remote-subprocess）回调业务服务沙箱接口的客户端。
 *
 * 执行世界：每个会话的工作目录是 `/sessions/<根会话 id>`（workpath.mjs 的 hostMount），会话从路径里认，
 * 请求里的路径一律是工作区路径（相对写法，根是空串）：宿主路径在发出之前换掉，业务服务按沙箱的挂载点放到容器里。
 * 令牌用本会话的工具令牌（受众 tools），业务服务按当前授权再判一次。
 */

import { getSession } from './state.mjs'

const DEFAULT_TIMEOUT_MS = 30_000

/** 业务服务拒绝或不可达。status 为 HTTP 状态（不可达时为 0），code 取业务服务的错误码。 */
export class SandboxCallError extends Error {
  constructor(message, code, status, options) {
    super(message, options)
    this.code = code
    this.status = status
  }
}

/** @param {{ baseUrl?: string, timeoutMs?: number }} [options] */
export function createSandboxClient(options = {}) {
  const root = (options.baseUrl ?? process.env.KNOWWORK_SANDBOX_BASE_URL
    ?? 'http://127.0.0.1:8001/internal/extensions/sandbox').replace(/\/+$/, '')
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS

  /**
   * @param {string} operation exec / stat / read / list / write
   * @param {string} sessionId 路径里的会话 id
   * @param {object} body
   * @param {{ signal?: AbortSignal, timeoutMs?: number }} [call]
   */
  async function request(operation, sessionId, body, call = {}) {
    const token = getSession(sessionId)?.toolAccess?.token
    if (token === undefined) throw new SandboxCallError('这个会话没有可用的沙箱', 'unavailable', 0)
    const deadline = AbortSignal.timeout(call.timeoutMs ?? timeoutMs)
    const signal = call.signal === undefined ? deadline : AbortSignal.any([call.signal, deadline])
    let response
    try {
      response = await fetch(`${root}/${operation}`, {
        method: 'POST',
        headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json' },
        body: JSON.stringify(body),
        signal,
      })
    } catch (error) {
      if (call.signal?.aborted) throw error
      throw new SandboxCallError('沙箱服务暂时不可用', 'unreachable', 0, { cause: error })
    }
    const payload = await response.json().catch(() => undefined)
    if (!response.ok) {
      const error = payload?.error
      throw new SandboxCallError(typeof error?.message === 'string' ? error.message : '沙箱操作失败',
        typeof error?.code === 'string' ? error.code : 'sandbox_error', response.status)
    }
    return payload ?? {}
  }

  return { request }
}
