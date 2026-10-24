/**
 * 知识库目录：照官方 dsh-tool-skill 的会话技能目录写法——本轮可查的知识库列 id、名字、截短的描述
 * 和能筛的字段名（「部门（文本）」），作为一条持久的用户角色 <system-reminder> 消息；可选值、标签集由模型按需调
 * knowledge_bases 取（和技能目录只列名字、正文用 skill 工具加载是同一个形态，不用过滤的回合不多花 token）。
 * 原来连字段名也不列，要多调一次才知道能按什么筛，模型多数时候直接搜、不筛。
 *
 * - 目录由业务服务随消息下发（sendMessage 存进根会话状态）；某一轮没下发就沿用上一轮的；
 * - 只给看得见 knowledge_search 的 agent（工具被收窄掉、子 Agent 没有这个工具就不给），判断方式同官方；
 * - 按内容摘要和会话里最近一份仍可见的目录比，没变就不再发，不改动已有的消息前缀（KV 缓存不失效）；
 *   变了就追加一份完整的替换目录并写明以这份为准；清空时也追加一份，叫模型别再用旧 id。
 * 名字和描述是管理员填的，照官方 escapeText 转义，免得把目录的标签提前闭合。
 */

import { createHash } from 'node:crypto'
import { createUserMessage } from '@deepseek-ai/dsh-llm'
import { escapeText } from '@deepseek-ai/dsh-skill'
import { resolveRootSession } from './state.mjs'

export const name = 'knowwork-knowledge-catalog'
export const inject = ['tools']

const KIND = 'knowwork-knowledge-catalog'
export const SEARCH_TOOL = 'knowledge_search'
const MAX_BASES = 100
const MAX_FIELDS = 30
const ID = /^[A-Za-z0-9_.:-]{3,200}$/
const EMPTY = Object.freeze({ bases: Object.freeze([]), omitted: 0 })

/** 校验业务服务下发的目录；没下发时返回 undefined。格式不对抛错，消息可以回给调用方。 */
export function catalogOf(value) {
  if (value === undefined || value === null) return undefined
  if (typeof value !== 'object' || !Array.isArray(value.bases) || value.bases.length > MAX_BASES) {
    throw new Error('knowledge 格式错误：要有 bases 数组，最多 100 项')
  }
  const bases = value.bases.map(entry => {
    const { id, name: title, description = '', fields = [] } = entry ?? {}
    if (typeof id !== 'string' || !ID.test(id)) throw new Error('knowledge 里的知识库 id 格式错误')
    if (typeof title !== 'string' || title.length === 0 || title.length > 200) throw new Error('knowledge 里的知识库名字格式错误')
    if (typeof description !== 'string' || description.length > 300) throw new Error('knowledge 里的知识库描述过长')
    if (!Array.isArray(fields) || fields.length > MAX_FIELDS
      || !fields.every(field => typeof field === 'string' && field.length > 0 && field.length <= 100)) {
      throw new Error('knowledge 里的字段格式错误：最多 30 个，每个是不超过 100 字的文字')
    }
    return Object.freeze({ id, name: title, description, fields: Object.freeze([...fields]) })
  })
  const omitted = value.omitted ?? 0
  if (!Number.isSafeInteger(omitted) || omitted < 0) throw new Error('knowledge.omitted 要是非负整数')
  return Object.freeze({ bases: Object.freeze(bases), omitted })
}

const digestOf = catalog => createHash('sha256')
  .update(JSON.stringify([catalog.bases.map(b => [b.id, b.name, b.description, b.fields]), catalog.omitted]))
  .digest('hex')

function entries(catalog) {
  const lines = catalog.bases.map(b => {
    const line = `- \`${b.id}\`：${escapeText(b.name)}`
    const described = b.description ? `${line}。${escapeText(b.description)}` : line
    return b.fields.length > 0 ? `${described}（可筛选：${b.fields.map(escapeText).join('、')}）` : described
  })
  if (catalog.omitted > 0) lines.push(`- 另有 ${catalog.omitted} 个知识库没有列出，可以用 knowledge_bases 查看`)
  return lines
}

function render(catalog, digest, update) {
  const body = catalog.bases.length === 0
    ? ['现在没有可以检索的知识库，不要再用之前目录里的知识库 id。']
    : [
        update ? '可以检索的知识库变了，这份完整目录取代之前所有的知识库目录：'
          : '本会话可以检索下面这些知识库。knowledge_search 不指定 knowledge_bases 时查全部；要限定范围，填这里的 id。',
        '',
        '<available_knowledge_bases>',
        ...entries(catalog),
        '</available_knowledge_bases>',
        '',
        '「可筛选」是这个库能按哪些元数据字段筛（knowledge_search 的 filters、tags）；要看可选值和标签集调 knowledge_bases。',
      ]
  return createUserMessage({
    content: [{ type: 'text', text: ['<system-reminder>', ...body, '</system-reminder>'].join('\n') }],
    source: { kind: KIND, digest, update },
  })
}

/** 会话里最近一份目录：是否发过、最近一份仍在可见历史里时它的摘要（压缩掉了就要重发）。 */
function history(agent) {
  const visible = new Set(agent.session.surface.nodes)
  let published = false
  for (let index = agent.session.seq - 1; index >= 0; index -= 1) {
    const event = agent.session.eventAt(index)
    if (event?.type !== 'user/message' || event.data.source?.kind !== KIND) continue
    const digest = event.data.source.digest
    if (typeof digest !== 'string') continue
    published = true
    if (visible.has(event.seq)) return { visibleDigest: digest, published }
  }
  return { published }
}

/** @param {import('@deepseek-ai/cordis').Context} ctx */
export function apply(ctx) {
  ctx.on('agent/pre-step', async ({ agent }, next) => {
    const decision = await next()
    if (decision.kind === 'reject') return decision
    const state = resolveRootSession(agent.session)
    const catalog = ctx.tools.get(SEARCH_TOOL, agent) !== undefined ? state?.knowledgeCatalog : EMPTY
    if (catalog === undefined) return decision
    const digest = digestOf(catalog)
    const past = history(agent)
    const queued = decision.messages.find(m => m.source?.kind === KIND)
    const without = () => (queued === undefined ? decision
      : { ...decision, messages: decision.messages.filter(m => m.id !== queued.id) })
    if (past.visibleDigest === digest) return without()
    if (queued?.source.digest === digest) return decision
    if (!past.published && catalog.bases.length === 0) return without()
    const message = render(catalog, digest, past.published)
    return {
      ...decision,
      messages: queued === undefined ? [...decision.messages, message]
        : decision.messages.map(m => (m.id === queued.id ? message : m)),
    }
  })
}
