/**
 * 远程执行器（remote-fs、remote-subprocess）回调业务服务沙箱接口的客户端。
 *
 * 执行世界：每个会话的工作目录是 `/sessions/<根会话 id>`，业务服务把它对到该会话容器的 /workspace，
 * 容器里同一路径是指向 /workspace 的符号链接。DSH 的 fs 与 subprocess 服务只认路径，所以会话从路径里认：
 * 子 Agent 继承父会话的工作目录，落到同一个容器。
 * 令牌用本会话的工具令牌（受众 tools），业务服务按当前授权再判一次。
 */

import { posix } from 'node:path'
import { getSession } from './state.mjs'

export const SESSIONS_ROOT = '/sessions'
const ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/
const DEFAULT_TIMEOUT_MS = 30_000

/** 业务服务拒绝或不可达。status 为 HTTP 状态（不可达时为 0），code 取业务服务的错误码。 */
export class SandboxCallError extends Error {
  constructor(message, code, status, options) {
    super(message, options)
    this.code = code
    this.status = status
  }
}

export function sessionRoot(sessionId) {
  return `${SESSIONS_ROOT}/${sessionId}`
}

/** 规范化一个绝对路径；不是 `/sessions/<id>` 下的返回 undefined。 */
export function sessionPath(path) {
  if (typeof path !== 'string' || !path.startsWith('/') || path.includes('\0')) return undefined
  const normal = posix.normalize(path).replace(/\/+$/, '') || '/'
  const parts = normal.split('/')
  if (parts[1] !== SESSIONS_ROOT.slice(1) || parts[2] === undefined || !ID_PATTERN.test(parts[2])) return undefined
  return { path: normal, sessionId: parts[2] }
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
