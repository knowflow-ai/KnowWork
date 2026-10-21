/**
 * 工具桥调用业务服务内部接口的客户端，以及工具描述的校验。
 *
 * 宿主插件回调业务服务统一走 `/internal/extensions/{plugin}/{operation}`（技术方案 3.9 节第 4 条），
 * 这里是 `tools` 插件的三个操作：`list`（本会话可用的工具描述）、`execute`（执行一次调用）、
 * `approval-grant`（这个工具在本会话是否已有「不再询问」的授权，审批应答者用）。
 * 业务服务返回的内容同样当作不可信输入校验：名称、说明、参数 Schema、风险等级、审批方式、使用规则都有格式与上限。
 * 审批方式缺省时按风险推导（只读不问，写入与高风险要问），与业务服务 ToolSpec.approval_mode 同一口径。
 */

const NAME_PATTERN = /^[A-Za-z][A-Za-z0-9_-]{0,63}$/
const RISKS = new Set(['read', 'write', 'dangerous'])
const APPROVALS = new Set(['ask', 'never'])
const MAX_GUIDANCE = 1000
const MAX_TOOLS = 128
const MAX_DESCRIPTION = 4000
const MAX_RESPONSE_BYTES = 4 * 1024 * 1024
const LIST_TIMEOUT_MS = 10_000
const GRANT_TIMEOUT_MS = 5_000

/** 业务服务拒绝或不可达。message 是可以给模型看的说明，不含内部细节。 */
export class BridgeError extends Error {}

/**
 * @typedef {object} BridgeTool
 * @property {string} name
 * @property {string} description
 * @property {object} inputSchema 对象根的 JSON Schema（官方 dsh-tools 支持的子集，注册时由官方再校验一次）
 * @property {'read' | 'write' | 'dangerous'} risk 真实风险等级，审计与展示用
 * @property {'ask' | 'never'} approval 执行前是否走官方人工审批
 * @property {string} [guidance] 写进系统提示的使用规则
 */

/** @returns {BridgeTool} */
function toolOf(raw) {
  if (typeof raw !== 'object' || raw === null) throw new BridgeError('工具描述格式错误')
  const { name, description, inputSchema, risk, guidance } = raw
  if (typeof name !== 'string' || !NAME_PATTERN.test(name)) throw new BridgeError('工具名称格式错误')
  if (typeof description !== 'string' || description.length === 0 || description.length > MAX_DESCRIPTION) {
    throw new BridgeError(`工具 ${name} 的说明缺失或过长`)
  }
  if (typeof inputSchema !== 'object' || inputSchema === null || inputSchema.type !== 'object') {
    throw new BridgeError(`工具 ${name} 的参数 Schema 必须是对象根`)
  }
  if (!RISKS.has(risk)) throw new BridgeError(`工具 ${name} 的风险等级必须是 read、write 或 dangerous`)
  const approval = raw.approval ?? (risk === 'read' ? 'never' : 'ask')
  if (!APPROVALS.has(approval)) throw new BridgeError(`工具 ${name} 的审批方式必须是 ask 或 never`)
  if (guidance !== undefined && (typeof guidance !== 'string' || guidance.length === 0 || guidance.length > MAX_GUIDANCE)) {
    throw new BridgeError(`工具 ${name} 的使用规则为空或过长`)
  }
  return guidance === undefined ? { name, description, inputSchema, risk, approval } : { name, description, inputSchema, risk, approval, guidance }
}

/** 校验业务服务返回的工具清单：数量上限、名称不重复。 */
export function toolsOf(body) {
  const list = body?.tools
  if (!Array.isArray(list) || list.length > MAX_TOOLS) throw new BridgeError('工具清单格式错误或数量超限')
  const tools = list.map(toolOf)
  const names = new Set(tools.map(t => t.name))
  if (names.size !== tools.length) throw new BridgeError('工具清单里有重名工具')
  return tools
}

async function readBody(response) {
  const text = await response.text()
  if (Buffer.byteLength(text) > MAX_RESPONSE_BYTES) throw new BridgeError('业务服务响应过大')
  try {
    return text.length === 0 ? {} : JSON.parse(text)
  } catch {
    throw new BridgeError('业务服务响应不是合法 JSON')
  }
}

/** 业务服务的错误说明是给模型看的固定文案；拿不到就用通用说法。 */
function errorMessageOf(body, status) {
  const message = body?.error?.message
  return typeof message === 'string' && message.length <= 500 ? message : `业务服务拒绝了这次调用（${status}）`
}

/**
 * @param {{ baseUrl: string, executeTimeoutMs: number }} options
 */
export function createBridgeClient({ baseUrl, executeTimeoutMs }) {
  const root = baseUrl.replace(/\/+$/, '')

  async function call(path, { token, method, body, headers, signal }) {
    let response
    try {
      response = await fetch(`${root}${path}`, {
        method,
        headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json', ...headers },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal,
      })
    } catch (error) {
      if (signal?.aborted) throw error
      throw new BridgeError('业务服务不可达')
    }
    const parsed = await readBody(response)
    if (!response.ok) throw new BridgeError(errorMessageOf(parsed, response.status))
    return parsed
  }

  return {
    /** 拉取本会话可用的工具描述。 */
    async list(token) {
      const body = await call('/list', { token, method: 'GET', signal: AbortSignal.timeout(LIST_TIMEOUT_MS) })
      return toolsOf(body)
    },

    /**
     * 执行一次调用。幂等键 = 实际发起调用的会话 ID + 调用 ID，业务服务再加上令牌里的根会话做前缀。
     * @returns {Promise<{ actionId: string, status: string, content: string, truncated: boolean, replayed: boolean }>}
     */
    async execute(token, { toolName, args, agentSessionId, callId, signal }) {
      const combined = AbortSignal.any([signal, AbortSignal.timeout(executeTimeoutMs)])
      const body = await call('/execute', {
        token,
        method: 'POST',
        body: { toolName, arguments: args ?? {}, agentSessionId, callId },
        headers: { 'idempotency-key': `${agentSessionId}:${callId}` },
        signal: combined,
      })
      if (typeof body.content !== 'string' || typeof body.status !== 'string') throw new BridgeError('业务服务响应格式错误')
      return body
    },

    /** 这个工具在本会话是否已有用户给的「本会话内同类操作不再询问」授权。只认明确的 true。 */
    async grantCovers(token, { toolName, signal }) {
      const combined = AbortSignal.any([signal ?? new AbortController().signal, AbortSignal.timeout(GRANT_TIMEOUT_MS)])
      const body = await call('/approval-grant', { token, method: 'POST', body: { toolName }, signal: combined })
      return body?.granted === true
    },
  }
}
