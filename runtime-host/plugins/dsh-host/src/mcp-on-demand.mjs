/**
 * MCP 工具按需加载：管理员给某个 MCP 服务开了「按需加载」，它的工具不随每次请求发给模型，系统提示里只列名字和描述的
 * 第一行，模型用 `mcp__<服务名>__load_tools` 把要用的加进自己的工具清单，工具多、描述长的服务因此不常驻上下文。
 *
 * 照官方 @deepseek-ai/dsh-mcp-client 0.2.3-alpha.2 的按需加载（`toolLoading: on-demand`；src/tool-loading.ts 的
 * McpToolLoader 与 src/server-context.ts 的目录段）：加载器的名字、说明与参数，目录段的名字、位置、标题与写法，
 * 描述截到 160 字，加载结果与报错的文字，都照官方。官方的只管它自己连的 MCP 服务；我们的 MCP 连接与凭据在业务服务
 * （DSH 里不放密钥），工具经工具桥进来，所以在工具桥这边照它的形态做一份：哪些工具按需、属于哪个服务，由业务服务在
 * 工具清单里标（`onDemand.server`）。
 *
 * 和官方一样：加载的工具只注册进调用方 Agent 自己的作用域；子 Agent、宿主重启后恢复的会话开始时都没有已加载的工具；
 * 服务自己叫 load_tools 的工具不列进目录、记一条警告。
 * 我们多的几条（工具桥本来的规矩）：被会话策略 deny 的、被技能认领的（等技能加载了再给，见 skill-unlock）不进目录；
 * 目录与加载器按这一步的回答方式筛（answer-mode），快速问答下没有可用的按需工具时加载器也不出现。
 */

import { truncateWithoutSplittingSurrogatePair } from '@deepseek-ai/dsh-output-retention'
import { allowedInMode } from './answer-mode.mjs'
import { resolveRootSession } from './state.mjs'

/** 加载器在服务的 `mcp__<服务名>__` 命名空间里的原名（官方 LOADER_RAW_NAME） */
export const LOADER_RAW_NAME = 'load_tools'
/** 目录里每个工具描述最多多少字（官方 catalogDescriptionMaxLength 的默认值） */
const DESCRIPTION_MAX_LENGTH = 160
/** 某个服务的加载器对模型的名字（同官方 publicToolName(serverName, 'load_tools')；服务名已在 tool-bridge-client 校验过、
 *  不含下划线，直接拼就是官方规范化后的名字） */
export const loaderNameOf = server => `mcp__${server}__${LOADER_RAW_NAME}`

/** 工具清单里一个工具是不是按需加载的，是的话属于哪个服务（标记已在 tool-bridge-client 校验过） */
export const onDemandServerOf = tool => tool.onDemand?.server

/**
 * 把按需加载的工具按服务分组。名字和该服务的加载器撞上的（服务自己有个 load_tools）不进目录，记一条警告（同官方）。
 * @returns {Map<string, object[]>} 服务名 → 它的按需工具
 */
export function groupOnDemand(tools, log) {
  const groups = new Map()
  for (const tool of tools) {
    const server = onDemandServerOf(tool)
    if (server === undefined) continue
    if (tool.name === loaderNameOf(server)) {
      log.warn('MCP server %s offers its own %s tool; it is left out of the on-demand catalog', server, LOADER_RAW_NAME)
      continue
    }
    groups.set(server, [...(groups.get(server) ?? []), tool])
  }
  return groups
}

/**
 * 给一个 Agent 挂上各个按需服务的加载器与目录段。
 * @param {object} agent 官方 Agent
 * @param {Map<string, object[]>} groups groupOnDemand 的结果（已去掉 deny 的与技能认领的）
 * @param {(agent: object, tool: object) => () => void} register 工具桥把一个业务工具注册进某个 Agent 的作用域（同一份定义、
 *   审批与回答方式条件），返回注销函数
 */
export function attachOnDemand(agent, groups, register) {
  if (groups.size === 0) return
  const root = resolveRootSession(agent.session)
  const prompt = agent.ctx.systemPrompt
  for (const [server, tools] of groups) {
    const offered = () => tools.filter(tool => allowedInMode(root, tool))
    const loader = loaderOf(server, offered, register)
    agent.ctx.tools.register(loader, { when: () => offered().length > 0 })
    prompt?.section({
      name: `mcp:${server}:tools`,
      order: prompt.getSectionOrder('MCP_SERVERS'),
      interpolate: false,
      text: () => catalogOf(server, offered()),
    })
  }
}

/** 一个服务的加载器：每个 Agent 各一份，记着这个 Agent 已经加载了哪些 */
function loaderOf(server, offered, register) {
  const name = loaderNameOf(server)
  /** @type {WeakMap<object, Set<string>>} Agent → 它已加载的工具名 */
  const loads = new WeakMap()
  return {
    name,
    description: `Add tools from MCP server ${server} to your tool list. The system prompt lists them under "On-demand tools from MCP server ${server}".`,
    parameters: {
      type: 'object',
      properties: { tools: { type: 'array', items: { type: 'string' }, description: 'Tool names exactly as that list shows them.' } },
      required: ['tools'],
    },
    output: { schema: { type: 'string' }, render: (_args, value) => [{ type: 'text', text: value }] },
    async execute(args, exec) {
      const agent = exec.agent
      if (agent === undefined) throw new Error(`${name} requires an agent`)
      const requested = Array.isArray(args?.tools) ? args.tools.map(String) : []
      if (requested.length === 0) throw new Error(`${name} requires at least one tool name`)
      const available = new Map(offered().map(tool => [tool.name, tool]))
      const unknown = requested.filter(tool => !available.has(tool))
      if (unknown.length > 0) {
        throw new Error(`MCP server ${server} does not offer ${unknown.map(tool => `"${tool}"`).join(', ')}; use names from its on-demand tool list`)
      }
      let loaded = loads.get(agent)
      if (loaded === undefined) {
        loaded = new Set()
        loads.set(agent, loaded)
      }
      const added = []
      const present = []
      for (const tool of new Set(requested)) {
        if (loaded.has(tool)) {
          present.push(tool)
          continue
        }
        register(agent, available.get(tool))
        loaded.add(tool)
        added.push(tool)
      }
      return [
        ...added.length > 0 ? [`Loaded: ${added.join(', ')}.`] : [],
        ...present.length > 0 ? [`Already loaded: ${present.join(', ')}.`] : [],
      ].join('\n')
    },
  }
}

/** 系统提示里的目录：按名字排，每行名字加描述的第一行（同官方 McpToolLoader.catalog） */
export function catalogOf(server, tools) {
  if (tools.length === 0) return ''
  const lines = [...tools].sort((a, b) => (a.name < b.name ? -1 : 1)).map((tool) => {
    const summary = summaryOf(tool.description ?? '')
    return summary === '' ? `- ${tool.name}` : `- ${tool.name}: ${summary}`
  })
  return [
    `### On-demand tools from MCP server ${server}`,
    `These tools are not in your tool list. Before calling one, load it with ${loaderNameOf(server)}.`,
    lines.join('\n'),
  ].join('\n\n')
}

/** 描述的第一行，空白压成一个空格，超长截断加省略号（同官方 McpToolLoader.summary） */
function summaryOf(description) {
  const [first = ''] = description.trim().split('\n', 1)
  const line = first.replaceAll(/\s+/g, ' ').trim()
  if (line.length <= DESCRIPTION_MAX_LENGTH) return line
  return `${truncateWithoutSplittingSurrogatePair(line, DESCRIPTION_MAX_LENGTH - 3)}...`
}
