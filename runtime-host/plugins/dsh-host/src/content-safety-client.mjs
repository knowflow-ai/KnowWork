/**
 * 内容检查插件（content-guard.mjs）回调业务服务的客户端：取策略摘要、送文字去查。
 *
 * 词表只在业务服务，这里只送文字、拿「拦不拦、拦了说什么」。令牌用这段会话的工具令牌（受众 tools），业务服务按令牌认出
 * 空间、人和会话，另按当前授权再判一次是不是空间成员。
 * 策略摘要按根会话缓存几秒（业务服务那边按版本缓存编好的词表，两边加起来保存后 5 秒内生效）；取不到时用上次的，
 * 一次都没取到过就当没开：会话打开时工具桥要先从同一个入口拉到工具清单，那里不通会话根本开不起来。
 * 地址没给（参数和 KNOWWORK_CONTENT_SAFETY_BASE_URL 都没有）就不查，见 content-guard.mjs 开头。
 */

const POLICY_TTL_MS = 3_000
const POLICY_TIMEOUT_MS = 5_000
const CHECK_TIMEOUT_MS = 10_000
// 一次请求最多送这么多字（业务服务的上限是 200 万，留余量），长文字切开送，切口重叠「最长词长 - 1」个字
const PIECE_CHARS = 200_000
const PIECES_PER_REQUEST = 8
const MAX_CACHED = 10_000

/** 检查接口不通或回了错：开了检查的不放行（不可用不等于通过） */
export class CheckUnavailable extends Error {}

/**
 * @typedef {{ enabled: boolean, input: boolean, output: boolean, holdback: number, version: number }} Policy
 * @typedef {{ blocked: false } | { blocked: true, message: string }} Verdict
 */

const OFF = Object.freeze({ enabled: false, input: false, output: false, holdback: 0, version: 0 })

function policyOf(raw) {
  if (typeof raw !== 'object' || raw === null || raw.enabled !== true) return OFF
  const holdback = Number.isInteger(raw.holdback) && raw.holdback >= 0 ? Math.min(raw.holdback, 255) : 255
  return Object.freeze({
    enabled: true, input: raw.input === true, output: raw.output === true, holdback,
    version: Number.isInteger(raw.version) ? raw.version : 0,
  })
}

/** 把一段文字切成不超过 PIECE_CHARS 的几片，相邻两片重叠 overlap 个 UTF-16 单元：跨切口的词在后一片里是完整的 */
export function piecesOf(text, overlap) {
  if (text.length <= PIECE_CHARS) return [text]
  const pieces = []
  const step = Math.max(PIECE_CHARS - overlap, 1)
  for (let start = 0; start < text.length; start += step) {
    pieces.push(text.slice(start, start + PIECE_CHARS))
    if (start + PIECE_CHARS >= text.length) break
  }
  return pieces
}

/** @param {{ baseUrl?: string, now?: () => number }} [options] */
export function createContentSafetyClient(options = {}) {
  const root = (options.baseUrl ?? process.env.KNOWWORK_CONTENT_SAFETY_BASE_URL ?? '').replace(/\/+$/, '')
  const now = options.now ?? Date.now
  /** 根会话 id → { policy, at } */
  const cache = new Map()

  async function call(path, token, init, timeoutMs, signal) {
    const deadline = AbortSignal.timeout(timeoutMs)
    const response = await fetch(`${root}${path}`, {
      ...init,
      headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json' },
      signal: signal === undefined ? deadline : AbortSignal.any([signal, deadline]),
    })
    const body = await response.json().catch(() => undefined)
    if (!response.ok) throw new CheckUnavailable(body?.error?.message ?? `内容检查接口回了 ${response.status}`)
    return body
  }

  /** @returns {Promise<Policy>} */
  async function policy(rootSessionId, token) {
    if (!root) return OFF
    const cached = cache.get(rootSessionId)
    if (cached !== undefined && now() - cached.at < POLICY_TTL_MS) return cached.policy
    try {
      const fresh = policyOf(await call('/policy', token, { method: 'GET' }, POLICY_TIMEOUT_MS))
      if (cache.size >= MAX_CACHED && !cache.has(rootSessionId)) cache.delete(cache.keys().next().value)
      cache.set(rootSessionId, { policy: fresh, at: now() })
      return fresh
    } catch {
      // 取不到也记下时间：接口挂住时不让每一步都等满超时，过了缓存时间再试
      const kept = cached?.policy ?? OFF
      cache.set(rootSessionId, { policy: kept, at: now() })
      return kept
    }
  }

  /**
   * 送几段文字去查，命中一段就停。
   * @param {string} token
   * @param {{ direction: 'input' | 'output' | 'tool_args', tool?: string, texts: string[], holdback: number, signal?: AbortSignal }} request
   * @returns {Promise<Verdict>}
   */
  async function check(token, { direction, tool, texts, holdback, signal }) {
    const pieces = texts.filter(text => text.length > 0).flatMap(text => piecesOf(text, holdback * 2))
    for (let start = 0; start < pieces.length; start += PIECES_PER_REQUEST) {
      const segments = pieces.slice(start, start + PIECES_PER_REQUEST)
      let body
      try {
        body = await call('/check', token, {
          method: 'POST',
          body: JSON.stringify({ direction, ...tool === undefined ? {} : { tool }, segments }),
        }, CHECK_TIMEOUT_MS, signal)
      } catch (error) {
        if (signal?.aborted) throw error
        if (error instanceof CheckUnavailable) throw error
        throw new CheckUnavailable('内容检查服务连不上', { cause: error })
      }
      if (body?.blocked === true) return { blocked: true, message: typeof body.message === 'string' ? body.message : '' }
    }
    return { blocked: false }
  }

  function forget(rootSessionId) {
    cache.delete(rootSessionId)
  }

  return { configured: Boolean(root), policy, check, forget }
}
