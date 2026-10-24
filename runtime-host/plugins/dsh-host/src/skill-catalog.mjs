/**
 * 技能清单与内容的拉取（技能提供方）。
 *
 * 宿主插件回调业务服务统一走 `/internal/extensions/{plugin}/{operation}`，这里是 `skills` 的两个操作：
 * - `GET /catalog`：本会话可用技能清单（已发布版本），每项带 allowedTools / completionTools / unlocks；
 * - `GET /content?name=&digest=`：一个技能的 SKILL.md 正文。
 * 令牌是业务服务签发的受众 skills 短期令牌，宿主只转交不解读。
 *
 * 业务服务返回的内容按不可信输入校验；正文按清单里的 contentDigest 做 SHA-256 校验，不符就不交给模型。
 * 清单按根会话只拉一次（ensureCatalog 记住进行中的请求），工具桥、技能提供方、技能门读同一份。
 */

import { createHash } from 'node:crypto'

const SKILL_NAME = /^[a-z0-9]+(?:-[a-z0-9]+)*$/
const TOOL_NAME = /^[A-Za-z][A-Za-z0-9_-]{0,63}$/
const DIGEST = /^[0-9a-f]{64}$/
const MAX_SKILLS = 64
const MAX_TOOLS = 64
const MAX_DESCRIPTION = 2000
const MAX_RESPONSE_BYTES = 2 * 1024 * 1024
const TIMEOUT_MS = 10_000

/** 业务服务拒绝、不可达或返回了不合格的内容。message 可以给模型看，不含内部细节。 */
export class SkillCatalogError extends Error {}

const sha256 = text => createHash('sha256').update(text, 'utf8').digest('hex')

function toolsOf(value, field, name) {
  if (!Array.isArray(value) || value.length > MAX_TOOLS || value.some(t => typeof t !== 'string' || !TOOL_NAME.test(t))) {
    throw new SkillCatalogError(`技能 ${name} 的 ${field} 格式错误`)
  }
  return Object.freeze([...new Set(value)])
}

/**
 * @typedef {object} CatalogSkill
 * @property {string} name
 * @property {string} description
 * @property {string} version
 * @property {string} digest 发布包摘要，拉内容时带上
 * @property {string} contentDigest SKILL.md 正文摘要
 * @property {readonly string[]} allowedTools
 * @property {readonly string[]} completionTools 任一成功即算完成
 * @property {readonly string[]} unlocks 本技能认领的专业工具：加载本技能前对模型不可见
 * @property {boolean} modelInvocable
 * @property {boolean} userInvocable
 * @property {string} scope
 */

/** @returns {CatalogSkill} */
function skillOf(raw) {
  if (typeof raw !== 'object' || raw === null) throw new SkillCatalogError('技能清单格式错误')
  const { name, description, version, digest, contentDigest, scope } = raw
  if (typeof name !== 'string' || name.length > 64 || !SKILL_NAME.test(name)) throw new SkillCatalogError('技能名称格式错误')
  if (typeof description !== 'string' || description.length === 0 || description.length > MAX_DESCRIPTION) {
    throw new SkillCatalogError(`技能 ${name} 的说明缺失或过长`)
  }
  if (typeof version !== 'string' || version.length === 0 || version.length > 64) throw new SkillCatalogError(`技能 ${name} 的版本格式错误`)
  if (!DIGEST.test(digest ?? '') || !DIGEST.test(contentDigest ?? '')) throw new SkillCatalogError(`技能 ${name} 的摘要格式错误`)
  const allowedTools = toolsOf(raw.allowedTools, 'allowedTools', name)
  const completionTools = toolsOf(raw.completionTools, 'completionTools', name)
  const unlocks = toolsOf(raw.unlocks, 'unlocks', name)
  if (unlocks.length > 0 && completionTools.length === 0) throw new SkillCatalogError(`技能 ${name} 解锁了专业工具却没有完成判据`)
  return Object.freeze({
    name, description, version, digest, contentDigest, allowedTools, completionTools, unlocks,
    modelInvocable: raw.modelInvocable !== false,
    userInvocable: raw.userInvocable !== false,
    scope: typeof scope === 'string' ? scope.slice(0, 32) : 'workspace',
  })
}

/** 校验整张清单：数量上限、名称唯一、一个专业工具只属于一个技能。 */
export function catalogOf(body) {
  const list = body?.skills
  if (!Array.isArray(list) || list.length > MAX_SKILLS) throw new SkillCatalogError('技能清单格式错误或数量超限')
  const skills = list.map(skillOf)
  if (new Set(skills.map(s => s.name)).size !== skills.length) throw new SkillCatalogError('技能清单里有重名技能')
  const owners = new Map()
  for (const skill of skills) {
    for (const tool of skill.unlocks) {
      if (owners.has(tool)) throw new SkillCatalogError(`工具 ${tool} 同时被技能 ${owners.get(tool)} 与 ${skill.name} 认领`)
      owners.set(tool, skill.name)
    }
  }
  return Object.freeze(skills)
}

async function readBody(response) {
  const text = await response.text()
  if (Buffer.byteLength(text) > MAX_RESPONSE_BYTES) throw new SkillCatalogError('业务服务响应过大')
  try {
    return text.length === 0 ? {} : JSON.parse(text)
  } catch {
    throw new SkillCatalogError('业务服务响应不是合法 JSON')
  }
}

export function createSkillClient({ baseUrl }) {
  const root = baseUrl.replace(/\/+$/, '')

  async function get(path, token, signal) {
    let response
    try {
      response = await fetch(`${root}${path}`, {
        headers: { authorization: `Bearer ${token}` },
        signal: signal === undefined ? AbortSignal.timeout(TIMEOUT_MS) : AbortSignal.any([signal, AbortSignal.timeout(TIMEOUT_MS)]),
      })
    } catch (error) {
      if (signal?.aborted) throw error
      throw new SkillCatalogError('业务服务不可达')
    }
    const body = await readBody(response)
    if (!response.ok) {
      const message = body?.error?.message
      throw new SkillCatalogError(typeof message === 'string' && message.length <= 500 ? message : `业务服务拒绝了请求（${response.status}）`)
    }
    return body
  }

  return {
    async catalog(token) {
      return catalogOf(await get('/catalog', token))
    },

    /** 拉一个技能的正文并按清单里的摘要校验。 */
    async content(token, skill, signal) {
      const query = new URLSearchParams({ name: skill.name, digest: skill.digest })
      const body = await get(`/content?${query}`, token, signal)
      if (body?.name !== skill.name || body?.digest !== skill.digest || typeof body?.content !== 'string') {
        throw new SkillCatalogError(`技能 ${skill.name} 的内容与清单不符`)
      }
      if (sha256(body.content) !== skill.contentDigest) throw new SkillCatalogError(`技能 ${skill.name} 的内容校验失败`)
      return body.content
    },
  }
}

let defaultClient
/** 宿主进程内共用的客户端；地址取环境变量 KNOWWORK_SKILLS_BASE_URL。 */
export function skillClient() {
  defaultClient ??= createSkillClient({
    baseUrl: process.env.KNOWWORK_SKILLS_BASE_URL ?? 'http://127.0.0.1:8001/internal/extensions/skills',
  })
  return defaultClient
}

/**
 * 根会话的技能清单，只拉一次。会话打开时没带技能令牌的返回空清单。
 * 拉取失败时抛出，调用方（agent/created）据此让会话打开失败（失败关闭）。
 * @returns {Promise<readonly CatalogSkill[]>}
 */
export function ensureCatalog(root) {
  if (root.skillAccess === undefined) return Promise.resolve(Object.freeze([]))
  root.skillCatalogPromise ??= skillClient().catalog(root.skillAccess.token).then(
    catalog => { root.skillCatalog = catalog; return catalog },
    error => {
      const reason = error instanceof SkillCatalogError ? error.message : '拉取失败'
      throw new Error(`knowwork-skills: 会话 ${root.id} 拉取技能清单失败：${reason}`)
    },
  )
  return root.skillCatalogPromise
}

/** 本会话清单里被认领的专业工具 → 认领它的技能名。 */
export function lockedTools(catalog) {
  return new Map(catalog.flatMap(skill => skill.unlocks.map(tool => [tool, skill.name])))
}
