/**
 * 用户这一轮在输入框里 @ 的东西（知识库、目录、文件、标签、MCP 服务、技能）：写成用户原话后面的一个说明块，
 * 和附件说明块一样在会话历史里保留，后面几轮模型仍知道那一问点名过什么。
 *
 * 第一行是固定前缀加 JSON，业务服务投影时按这一行认出来、换成气泡上的小标签（server conversations/events_v1.py）；
 * 后面几行写给模型。检索范围由业务服务在执行 knowledge_search 时强制限定，这里只是告诉模型不用自己再加条件。
 * 技能照 DSH 官方的显式调用手势写成「/技能名」，前后留空白（dsh-tool-skill 的 SKILL_GESTURE 只认这样的），
 * 官方插件据此把技能说明注入这一步，和用户自己在原话里打 /技能名 一样。
 * MCP 服务：这一轮优先用这个服务的工具，但不排斥别的资料和工具，
 * 点名也不代表授权做无关的操作；工具给不给、能不能调仍由业务服务按授权与连接决定。
 */

export const MENTION_PREFIX = '[用户这一轮 @ 点名的范围] '
export const MAX_MENTIONS = 30
const TYPES = new Set(['knowledge_base', 'folder', 'document', 'tag', 'mcp', 'skill'])
const KNOWLEDGE_TYPES = new Set(['knowledge_base', 'folder', 'document', 'tag'])
const SKILL_NAME = /^[a-z0-9]+(?:-[a-z0-9]+)*$/
const MCP_SERVER = /^(ent_)?[a-z][a-z0-9-]{0,23}$/ // 服务标识，工具名前缀 mcp__<标识>__ 里那段；企业 MCP 带 ent_ 前缀
const MAX_NAME = 200
const MAX_ID = 300

function invalid(message) {
  return Object.assign(new Error(message), { code: 'invalid_request' })
}

function boundedName(value, field, max = MAX_NAME) {
  if (typeof value !== 'string' || value.length === 0 || value.length > max || /[\r\n]/.test(value)) {
    throw invalid(`mentions.${field} 格式错误`)
  }
  return value
}

/** 校验业务服务给的 @ 清单；不合法直接拒绝整条消息。 */
export function mentionsOf(raw) {
  if (raw === undefined) return []
  if (!Array.isArray(raw) || raw.length > MAX_MENTIONS) throw invalid(`mentions 最多 ${MAX_MENTIONS} 项`)
  return raw.map((item) => {
    if (typeof item !== 'object' || item === null || !TYPES.has(item.type)) throw invalid('mentions.type 格式错误')
    const id = boundedName(item.id, 'id', MAX_ID)
    const name = boundedName(item.name, 'name')
    if (item.type === 'skill' && !SKILL_NAME.test(id)) throw invalid('mentions.id 不是技能名')
    if (item.type === 'mcp' && !MCP_SERVER.test(id)) throw invalid('mentions.id 不是 MCP 服务标识')
    if (item.base === undefined) return { type: item.type, id, name }
    return { type: item.type, id, name, base: boundedName(item.base, 'base'), baseName: boundedName(item.baseName, 'baseName') }
  })
}

/** 用户原话之后的说明块；没有 @ 时不加。 */
export function mentionBlocks(mentions) {
  if (mentions.length === 0) return []
  const lines = [MENTION_PREFIX + JSON.stringify(mentions)]
  if (mentions.some(m => KNOWLEDGE_TYPES.has(m.type))) {
    lines.push('knowledge_search 已由系统限定在上面点名的知识库、目录、文件和标签里（点名了文件、标签的知识库只查它们），'
      + '直接检索即可，不用自己再加这些条件。')
  }
  const services = mentions.filter(m => m.type === 'mcp').map(m => `@${m.name}（工具名以 mcp__${m.id}__ 开头）`)
  if (services.length > 0) {
    lines.push(`用户点名这一轮优先用这些外部服务：${services.join('、')}。回答前先用其中和问题相关的工具；`
      + '这不代替查其他资料，也不排斥别的可用工具，点名也不代表可以做与任务无关的操作；用户之后明确的要求优先。'
      + '如果你手上没有这些工具，告诉用户这个服务现在用不了，可以到「设置 → 连接」看看。')
  }
  const skills = mentions.filter(m => m.type === 'skill').map(m => `/${m.id}`)
  if (skills.length > 0) lines.push(`用户点名的技能： ${skills.join(' ')} `)
  return [{ type: 'text', text: lines.join('\n') }]
}
