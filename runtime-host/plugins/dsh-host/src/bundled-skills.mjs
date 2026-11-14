/**
 * KnowWork 自带的技能（plugins/dsh-host/skills/<名字>/SKILL.md），交给 DSH 官方技能注册表。
 *
 * 现在是三个 Office 技能（Word、Excel、PPT），照搬自 KnowFlow（knowflow/server/deep_agent/skills/office-*），
 * 画示意图的 diagram（对话界面把 mermaid 代码块画成图，原来常驻在系统提示里，2026-11-09 改成要用时才加载），
 * 和读文档的 read-document（官方 read 只读纯文本，PDF、Office 文件经辅助脚本 office/extract_text.py 转成文字再读）。
 * Office 技能用原生 LibreOffice、pandoc、docx-js、pptxgenjs 与辅助脚本（skills/resources/office，照官方技能包的写法：
 * 正文里写相对路径 `office/xxx.py`，官方加载技能时告诉模型资源目录，按它解析）。不用 DSH 官方的 dsh-skill-office：它绑定的
 * LibreOffice Kit 在 Linux 上只有 WASM 引擎，又慢又吃内存；它也没有修订模式、PPT 设计与必做质检这些内容。
 *
 * 资源目录是执行环境的差别（同官方 dsh-skill-office 的 assetRoot 配置）：本机宿主上就是插件包里的 skills/resources；
 * 服务端宿主上命令在会话容器里跑，环境层配成沙箱镜像里的 /opt/knowwork（镜像把同一份脚本拷到 /opt/knowwork/office，
 * 业务服务的格式转换与重算也用它），不把宿主机路径告诉模型。工具链在服务端装在沙箱镜像里，本机用这台电脑上装的。
 *
 * 技能按目录发现（有 SKILL.md 的子目录），新增一个自带技能只要加目录。正文按需读。
 * 候选的形状、等级、调用权限与官方随包技能一致。
 *
 * 按会话注册（同 skill-provider：用官方 createScope 给 agent 铸一个作用域再 registerProvider），不注册到全局层：
 * 空间管理员可以在本空间停用自带技能，停用的名字随本会话技能清单一起拿到（root.bundledOff），这个会话就不注册它们；
 * 和会话清单里的技能（公共、空间、个人）同名的也不注册，同名时只给清单里那份。没带技能令牌的会话全部注册。
 */

import { readFileSync, readdirSync } from 'node:fs'
import { readFile } from 'node:fs/promises'
import { isAbsolute, join, sep } from 'node:path'
import { fileURLToPath } from 'node:url'
import { BUNDLED_SKILL_RANK } from '@deepseek-ai/dsh-skill'
import { createScope } from '@deepseek-ai/dsh-scope'
import { ensureCatalog } from './skill-catalog.mjs'
import { resolveRootSession } from './state.mjs'

export const name = 'knowwork-bundled-skills'
export const inject = ['skills']

export const SKILL_ROOT = fileURLToPath(new URL('../skills/', import.meta.url))
// 自带技能共用的资源目录（Office 辅助脚本在它下面的 office/）：本机宿主默认就用这一份，服务端宿主由环境层改配置
export const RESOURCE_ROOT = join(SKILL_ROOT, 'resources')
// 用到辅助脚本的技能（Office 技能和读文档）才报资源目录；diagram 没有资源，不报（报了模型加载技能时会看到一个用不着的路径）
const withHelpers = skillName => skillName.startsWith('office-') || skillName === 'read-document'

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

/** 候选带上这个执行环境里的资源目录（用到辅助脚本的才有）。 */
function withResources(candidates, resourceRoot) {
  const resourceBase = Object.freeze({ kind: 'directory', path: resourceRoot.split(sep).join('/') })
  return candidates.map(candidate => (withHelpers(candidate.name) ? { ...candidate, resourceBase } : candidate))
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

/**
 * @param {import('@deepseek-ai/cordis').Context} ctx
 * @param {{ resourceRoot?: string }} [config] 自带技能的资源目录在执行环境里的位置（绝对路径），不给用插件包里的
 */
export function apply(ctx, config = {}) {
  const resourceRoot = config?.resourceRoot ?? RESOURCE_ROOT
  if (!isAbsolute(resourceRoot) && !resourceRoot.startsWith('/')) throw new Error('bundled-skills: resourceRoot must be absolute')
  /** agent → 为它铸的作用域（释放时撤销提供方）。 */
  const scopes = new WeakMap()

  ctx.on('agent/created', async ({ agent }) => {
    const root = resolveRootSession(agent.session)
    // 拉清单失败时 skill-provider 让会话打开失败；这里什么都不注册（失败关闭）
    const catalog = root === undefined ? [] : await ensureCatalog(root).catch(() => undefined)
    if (catalog === undefined) return
    const candidates = withResources(bundledFor(root, catalog), resourceRoot)
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
