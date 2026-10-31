/**
 * 技能提供方：把业务服务给的「本会话可用技能清单（已发布版本）」交给 DSH 官方技能机制。
 *
 * 只用官方能力：
 * - 技能注册表 `@deepseek-ai/dsh-skill` 是「全局 + 按作用域分层」的，层按作用域键（官方用 agent 对象本身）
 *   归档；官方 `skill` 工具（`@deepseek-ai/dsh-tool-skill`）以 `scope: agent` 查目录与加载。
 *   `agent.ctx` 继承的是 agent-loop 的依赖，没有注入 skills，所以用官方 `createScope(ctx, agent)`
 *   从本插件的上下文铸一个同键的作用域，经它 `registerProvider()`，提供方就只落在该 agent 的层：
 *   每个会话只看到自己的技能，与工具桥按 agent 注册业务工具是同一个形态。agent 释放时撤销。
 *   目录消息、`skill` 工具、用户 `/技能名` 显式调用都由官方插件负责；
 * - 内容按需拉取：模型加载某个技能时才回调业务服务 `GET /content`，按会话打开时的版本摘要钉住，
 *   拉到的正文按清单里的 contentDigest 校验；
 * - 本会话（清单与自带技能，见 bundled-skills）与全局都没有技能时，用官方 `restrict` 收掉 `skill` 工具，不给模型一个空工具。
 *
 * 专业工具的解锁：模型经 `skill` 工具成功加载技能（官方 `tools/result`），或用户 `/技能名` 显式调用
 * 之后，解锁该技能认领的专业工具（skill-unlock）。
 * 显式调用由官方 tool-skill 在 `agent/pre-step` 里注入正文，但这一步的工具清单在 pre-step 之前就已组装，
 * 等看到注入再解锁就晚了一步。所以按官方同一条规则（tool-skill 的 SKILL_GESTURE：前后是空白的
 * `/kebab-name`，只认用户可调用的技能）识别用户消息里的 `/技能名`，分两处处理：
 * - 消息进收件箱时（会话事件 `agent/inbox/spliced`，早于该回合的组装）就解锁专业工具，
 *   模型第一次请求就能用上；
 * - 消息在回合里写进会话日志时（`user/message`）才记进本轮的技能门，因为 `turn/start` 会清空上一轮的记录。
 */

import { createScope } from '@deepseek-ai/dsh-scope'
import { bundledFor } from './bundled-skills.mjs'
import { skillClient, ensureCatalog, lockedTools } from './skill-catalog.mjs'
import { lockTools, unlockSkill } from './skill-unlock.mjs'
import { publish, resolveRootSession } from './state.mjs'

export const name = 'knowwork-skill-provider'
export const inject = ['agents', 'tools', 'skills']

const PROVIDER = 'knowwork'
const SKILL_TOOL = 'skill'

function candidateOf(skill) {
  return {
    name: skill.name,
    description: skill.description,
    invocation: { modelInvocable: skill.modelInvocable, userInvocable: skill.userInvocable },
    source: `knowwork:${skill.scope}`,
    provider: PROVIDER,
    rank: 100,
    locator: Object.freeze({ name: skill.name, digest: skill.digest }),
    metadata: Object.freeze({ version: skill.version, digest: skill.digest }),
  }
}

/** 一个会话（agent）的技能提供方。令牌每次现取，续期后下一次加载即生效。 */
function providerFor(root, catalog) {
  const byName = new Map(catalog.map(skill => [skill.name, skill]))
  return {
    name: PROVIDER,
    list: async () => catalog.map(candidateOf),
    get: async (candidate, options) => {
      const skill = byName.get(candidate?.locator?.name)
      if (skill === undefined || candidate.locator.digest !== skill.digest || root.skillAccess === undefined) return undefined
      const content = await skillClient().content(root.skillAccess.token, skill, options?.signal)
      return {
        name: skill.name,
        description: skill.description,
        invocation: { modelInvocable: skill.modelInvocable, userInvocable: skill.userInvocable },
        source: `knowwork:${skill.scope}`,
        provider: PROVIDER,
        resourceBase: { kind: 'opaque', description: `KnowWork 技能 ${skill.name} ${skill.version}` },
        metadata: candidate.metadata,
        content,
      }
    },
  }
}

/** @param {import('@deepseek-ai/cordis').Context} ctx */
export function apply(ctx) {
  /** 宿主会话 id → 根 agent，用来把会话事件里的显式调用对应回 agent。 */
  const rootAgents = new Map()
  /** agent → 为它铸的作用域（释放时撤销提供方）。 */
  const scopes = new WeakMap()

  function loaded(agent, root, skillName, mode) {
    const skill = root.skillCatalog?.find(s => s.name === skillName)
    if (skill === undefined) return
    unlockSkill(agent, skillName)
    root.loadedSkills.add(skillName)  // 之后这个会话跑的命令带上它的环境变量（业务服务注入，宿主不经手值）
    const unlocked = [...skill.unlocks]
    const sessionId = String(agent.session.id)
    // 只有声明了完成判据的技能才进技能门；子 Agent 加载的不约束根会话的最终回答
    if (sessionId === root.id && skill.completionTools.length > 0) root.gate.loaded.set(skillName, skill.completionTools)
    publish(root.id, 'skill/loaded', { sessionId, skill: skillName, version: skill.version, mode, unlocked })
  }

  ctx.on('agent/created', async ({ agent }) => {
    const root = resolveRootSession(agent.session)
    if (root === undefined) return
    const catalog = await ensureCatalog(root)
    if (catalog.length === 0) {
      // 这个会话也没有自带技能（按会话注册，本空间可能全停用了），全局也没有，才收掉 skill 工具
      const global = await ctx.skills.list().catch(() => [])
      const none = global.length === 0 && bundledFor(root, catalog).length === 0
      if (none && ctx.tools.get(SKILL_TOOL) !== undefined) agent.ctx.tools.restrict({ deny: [SKILL_TOOL] })
      return
    }
    lockTools(agent, lockedTools(catalog))
    const scope = createScope(ctx, agent)
    scopes.set(agent, scope)
    scope.ctx.skills.registerProvider(() => providerFor(root, catalog))
    if (root.id === String(agent.session.id)) rootAgents.set(root.id, agent)
  })

  ctx.on('agent/disposed', async ({ agent }) => {
    const id = String(agent.session.id)
    if (rootAgents.get(id) === agent) rootAgents.delete(id)
    const scope = scopes.get(agent)
    scopes.delete(agent)
    await scope?.dispose()
  })

  ctx.on('tools/result', (exec, result) => {
    if (exec.name !== SKILL_TOOL || exec.agent === undefined || result.isError !== false) return undefined
    if (ctx.tools.get(SKILL_TOOL, exec.agent) === undefined) return undefined
    const root = resolveRootSession(exec.agent.session)
    const skillName = exec.arguments?.name
    if (root !== undefined && typeof skillName === 'string') loaded(exec.agent, root, skillName, 'automatic')
    return undefined
  })

  ctx.on('session/event', (session, event) => {
    const agent = rootAgents.get(String(session.id))
    const root = agent === undefined ? undefined : resolveRootSession(session)
    if (root?.skillCatalog === undefined) return
    const invoked = message => invokedSkillNames(message).filter(
      name => root.skillCatalog.some(s => s.name === name && s.userInvocable))
    if (event.type === 'agent/inbox/spliced') {
      for (const message of event.data?.inserted ?? []) {
        if (message?.source?.kind === 'user') for (const name of invoked(message)) unlockSkill(agent, name)
      }
    } else if (event.type === 'user/message' && event.data?.source?.kind === 'user') {
      for (const name of invoked(event.data)) loaded(agent, root, name, 'manual')
    }
  })
}

/** 与官方 tool-skill 的 SKILL_GESTURE 相同：前后是空白（或行首行尾）的 `/kebab-name`。 */
const SKILL_GESTURE = /(^|\s)\/([a-z0-9]+(?:-[a-z0-9]+)*)(?=\s|$)/g

function invokedSkillNames(message) {
  const names = new Set()
  for (const block of Array.isArray(message?.content) ? message.content : []) {
    if (block?.type !== 'text' || typeof block.text !== 'string') continue
    for (const match of block.text.matchAll(SKILL_GESTURE)) names.add(match[2])
  }
  return [...names]
}
