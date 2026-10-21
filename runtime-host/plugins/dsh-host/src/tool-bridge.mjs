/**
 * 工具桥：把业务服务的工具接进 DSH 会话（技术方案 3.4、7.2 节）。
 *
 * - 会话打开时（官方 `agent/created`，串行且会等待监听者），用业务服务签发的短期令牌（受众 tools）
 *   拉取本会话可用的工具描述，用 `agent.ctx.tools.register()` 注册到**该 agent 的作用域层**。
 *   官方注册表支持按 agent 注册（作用域工具随 agent 释放自动注销，且只对该 agent 及其子 Agent 可见），
 *   所以不必全局注册再按会话 restrict 收窄，也不会让一个会话的工具出现在另一个会话里。
 * - 子 Agent 按会话头 parentSession 找到根会话，用根会话拉到的同一份描述注册到自己的作用域层，
 *   执行时带根会话的令牌；幂等键里的会话是实际发起调用的子会话。
 * - 是否审批看业务服务给的 `approval`（与风险等级分开）：`ask` 的工具在官方 `tools/pre-execute`
 *   返回 ask，由官方审批服务 `dsh-user-approval` 转给审批应答者（approval-broker）；`never` 直接放行。
 *   沙箱里的命令与文件工具是 never（照 WeKnora，隔离由容器负责），风险等级仍按真实值给审计与展示。
 * - 工具的 `guidance` 用官方 `systemPrompt.section` 注册到该 agent 的作用域层（同 dsh-mcp-client 注册
 *   MCP 服务说明的做法）；文本在每次组装时按该 agent 实际可见的业务工具求值，同一条规则只写一次，
 *   被 deny 或尚未被技能解锁的工具的规则不出现。
 * - 执行时回调 `POST /internal/extensions/tools/execute`，业务服务按令牌重新判定可用性，
 *   查或建动作回执后执行。同一调用重复执行时业务服务返回第一次的结果，不重复执行。
 *
 * - 被本会话某个技能认领的专业工具先不注册，模型加载了认领它的技能之后才注册（技术方案 6.3 节，见 skill-unlock）。
 * - 会话策略 deny 里的业务工具根本不注册（官方 restrict 管不到作用域自己注册的工具，见 tool-deny）；
 *   deny 里既不是全局工具也不是本会话业务工具的名字，忽略并记一条宿主日志。
 *
 * 拉取失败、描述不合法、与全局工具重名时，会话打开失败（失败关闭），不会带着残缺的工具清单运行。
 */

import { BridgeError, createBridgeClient } from './tool-bridge-client.mjs'
import { ensureCatalog, lockedTools } from './skill-catalog.mjs'
import { defer, lockTools } from './skill-unlock.mjs'
import { resolveRootSession } from './state.mjs'
import { denyOf, isDenied, unknownDenied } from './tool-deny.mjs'

export const name = 'knowwork-tool-bridge'
export const inject = ['agents', 'tools', 'systemPrompt']

const DEFAULT_EXECUTE_TIMEOUT_MS = 300_000
const RISK_LABEL = { read: '只读', write: '写入', dangerous: '高风险' }
const GUIDANCE_SECTION = 'knowwork:tool-guidance'

/** @param {import('@deepseek-ai/cordis').Context} ctx @param {{ baseUrl?: string, executeTimeoutMs?: number }} [config] */
export function apply(ctx, config) {
  const baseUrl = config?.baseUrl ?? process.env.KNOWWORK_TOOLS_BASE_URL ?? 'http://127.0.0.1:8001/internal/extensions/tools'
  const executeTimeoutMs = config?.executeTimeoutMs ?? Number(process.env.KNOWWORK_TOOLS_TIMEOUT_MS ?? DEFAULT_EXECUTE_TIMEOUT_MS)
  if (!Number.isInteger(executeTimeoutMs) || executeTimeoutMs <= 0) {
    throw new Error(`knowwork-tool-bridge: executeTimeoutMs 必须是正整数毫秒，当前为 ${executeTimeoutMs}`)
  }
  const client = createBridgeClient({ baseUrl, executeTimeoutMs })
  const log = ctx.logger
  /** 工具桥注册的定义对象 → 描述。用对象身份判断一次调用是不是业务工具，不按名字猜。 */
  const owned = new WeakMap()

  function definitionOf(tool) {
    const definition = {
      name: tool.name,
      description: tool.description,
      parameters: tool.inputSchema,
      output: { schema: { type: 'string' }, render: (_args, value) => [{ type: 'text', text: String(value) }] },
      async execute(args, exec) {
        const root = resolveRootSession(exec.agent.session)
        if (root?.toolAccess === undefined) throw new Error('会话已释放，工具不可用')
        const result = await client.execute(root.toolAccess.token, {
          toolName: tool.name,
          args,
          agentSessionId: String(exec.agent.session.id),
          callId: String(exec.callId),
          signal: exec.signal,
        })
        if (result.status !== 'succeeded') throw new Error(result.content)
        return result.truncated ? `${result.content}\n[结果过长已截断，完整结果已存档：${result.actionId}]` : result.content
      },
    }
    owned.set(definition, tool)
    return definition
  }

  ctx.on('agent/created', async ({ agent }) => {
    const root = resolveRootSession(agent.session)
    if (root === undefined) return
    // 被技能认领的专业工具先不注册，等模型加载了认领它的技能再注册（skill-unlock）
    const owners = lockedTools(await ensureCatalog(root))
    lockTools(agent, owners)
    const isRoot = root.id === String(agent.session.id)
    if (isRoot) {
      root.bridgeTools = root.toolAccess === undefined ? [] : await fetchTools(root)
      reportUnknownDenied(root)
    }
    for (const tool of root.bridgeTools ?? []) {
      if (isDenied(root, tool.name)) continue
      const register = () => { agent.ctx.tools.register(definitionOf(tool)) }
      if (owners.has(tool.name)) defer(agent, tool.name, register)
      else register()
    }
    registerGuidance(agent, root.bridgeTools ?? [])
  })

  /** 该 agent 此刻可见的业务工具的使用规则，去重后按工具顺序拼接。 */
  function guidanceFor(agent, tools) {
    const visible = tools.filter(t => t.guidance !== undefined && owned.get(ctx.tools.get(t.name, agent)) !== undefined)
    return [...new Set(visible.map(t => t.guidance))].join('\n\n')
  }

  function registerGuidance(agent, tools) {
    const prompt = agent.ctx.systemPrompt
    if (prompt === undefined || !tools.some(t => t.guidance !== undefined)) return
    prompt.section({
      name: GUIDANCE_SECTION,
      order: prompt.getSectionOrder('TOOLS_SDK'),
      interpolate: false,
      text: () => guidanceFor(agent, tools),
    })
  }

  function reportUnknownDenied(root) {
    const business = new Set(root.bridgeTools.map(t => t.name))
    const unknown = unknownDenied(denyOf(root), name => ctx.tools.get(name) !== undefined, business)
    if (unknown.length > 0) log.warn('session %s policy denies unknown tools, ignored: %s', root.id, unknown.join(', '))
  }

  async function fetchTools(root) {
    let tools
    try {
      tools = await client.list(root.toolAccess.token)
    } catch (error) {
      const reason = error instanceof BridgeError ? error.message : '拉取失败'
      throw new Error(`knowwork-tool-bridge: 会话 ${root.id} 拉取业务工具失败：${reason}`)
    }
    // 作用域工具会遮住同名全局工具（官方规则），不允许业务工具借此顶替 subagent 一类的内置工具。
    const clash = tools.find(t => ctx.tools.get(t.name) !== undefined)
    if (clash !== undefined) throw new Error(`knowwork-tool-bridge: 业务工具 ${clash.name} 与宿主内置工具重名`)
    return tools
  }

  ctx.on('tools/pre-execute', async (exec, next) => {
    const decision = await next()
    if (decision.kind !== 'allow' || exec.agent === undefined) return decision
    const tool = owned.get(ctx.tools.get(exec.name, exec.agent))
    if (tool === undefined || tool.approval === 'never') return decision
    return { kind: 'ask', reason: `${RISK_LABEL[tool.risk]}操作「${tool.name}」需要确认` }
  })
}
