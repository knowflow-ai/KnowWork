/**
 * KnowWork 自带的技能（plugins/dsh-host/skills/<名字>/SKILL.md），交给 DSH 官方技能注册表。
 *
 * 现在是三个 Office 技能（Word、Excel、PPT），照搬自 KnowFlow（knowflow/server/deep_agent/skills/office-*），
 * 用原生 LibreOffice、pandoc、docx-js、pptxgenjs 与 /opt/knowwork/office 下的辅助脚本——这些都装在沙箱镜像里
 * （deploy/docker/sandbox.Dockerfile）。不用 DSH 官方的 dsh-skill-office：它绑定的 LibreOffice Kit 在 Linux 上只有
 * WASM 引擎，又慢又吃内存；它也没有修订模式、PPT 设计与必做质检这些内容。
 *
 * 技能按目录发现，新增一个自带技能只要加目录。正文按需读；资源目录报容器里的脚本目录，不把宿主机路径告诉模型。
 * 候选的形状、等级、调用权限与官方随包技能一致。
 *
 * 按会话注册（同 skill-provider：用官方 createScope 给 agent 铸一个作用域再 registerProvider），不注册到全局层：
 * 空间管理员可以在本空间停用自带技能，停用的名字随本会话技能清单一起拿到（root.bundledOff），这个会话就不注册它们；
 * 和会话清单里的技能（公共、空间、个人）同名的也不注册，同名时只给清单里那份。没带技能令牌的会话全部注册。
 */

import { readFileSync, readdirSync } from 'node:fs'
import { readFile } from 'node:fs/promises'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { BUNDLED_SKILL_RANK } from '@deepseek-ai/dsh-skill'
import { createScope } from '@deepseek-ai/dsh-scope'
import { ensureCatalog } from './skill-catalog.mjs'
import { resolveRootSession } from './state.mjs'

export const name = 'knowwork-bundled-skills'
export const inject = ['skills']

export const SKILL_ROOT = fileURLToPath(new URL('../skills/', import.meta.url))
const RESOURCE_BASE = '/opt/knowwork/office'

/** 拆出 frontmatter 的 description 与正文（frontmatter 只用单行的 name、description）。 */
export function parseSkill(raw, path) {
  const match = /^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/u.exec(raw)
  if (match === null) throw new Error(`bundled-skills: ${path} has no YAML frontmatter`)
  const line = /^description:\s*(.+)$/mu.exec(match[1])?.[1]?.trim()
  const description = line?.startsWith('"') ? JSON.parse(line) : line
  if (typeof description !== 'string' || description.length === 0) throw new Error(`bundled-skills: ${path} has no description`)
  return { description, content: raw.slice(match[0].length).trim() }
}

export function bundledSkillNames(root = SKILL_ROOT) {
  return readdirSync(root, { withFileTypes: true })
    .filter(entry => entry.isDirectory())
    .map(entry => entry.name)
    .filter(skillName => {
      try {
        readFileSync(join(root, skillName, 'SKILL.md'))
        return true
      } catch {
        return false
      }
    })
    .sort()
}

let candidatesCache
/** 自带技能的候选（进程内只读一次目录）。 */
export function bundledCandidates() {
  candidatesCache ??= Object.freeze(bundledSkillNames().map(skillName => {
    const locator = join(SKILL_ROOT, skillName, 'SKILL.md')
    const { description } = parseSkill(readFileSync(locator, 'utf8'), locator)
    return Object.freeze({
      name: skillName,
      description,
      invocation: { modelInvocable: true, userInvocable: true },
      provider: 'knowwork-bundled',
      source: 'bundled',
      rank: BUNDLED_SKILL_RANK,
      resourceBase: { kind: 'directory', path: RESOURCE_BASE },
      locator,
    })
  }))
  return candidatesCache
}

/** 这个会话要给的自带技能：去掉本空间停用的、和会话清单里同名的。 */
export function bundledFor(root, catalog = root?.skillCatalog ?? []) {
  const hidden = new Set([...(root?.bundledOff ?? []), ...catalog.map(skill => skill.name)])
  return bundledCandidates().filter(candidate => !hidden.has(candidate.name))
}

function providerOf(candidates) {
  return {
    name: 'knowwork-bundled',
    list: () => Promise.resolve(candidates),
    async get(candidate, options) {
      const { rank: _rank, locator, ...summary } = candidate
      const raw = await readFile(locator, { encoding: 'utf8', signal: options?.signal })
      return { ...summary, content: parseSkill(raw, locator).content }
    },
  }
}

/** @param {import('@deepseek-ai/cordis').Context} ctx */
export function apply(ctx) {
  /** agent → 为它铸的作用域（释放时撤销提供方）。 */
  const scopes = new WeakMap()

  ctx.on('agent/created', async ({ agent }) => {
    const root = resolveRootSession(agent.session)
    // 拉清单失败时 skill-provider 让会话打开失败；这里什么都不注册（失败关闭）
    const catalog = root === undefined ? [] : await ensureCatalog(root).catch(() => undefined)
    if (catalog === undefined) return
    const candidates = bundledFor(root, catalog)
    if (candidates.length === 0) return
    const scope = createScope(ctx, agent)
    scopes.set(agent, scope)
    scope.ctx.skills.registerProvider(() => providerOf(candidates))
  })

  ctx.on('agent/disposed', async ({ agent }) => {
    const scope = scopes.get(agent)
    scopes.delete(agent)
    await scope?.dispose()
  })
}
