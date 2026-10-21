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
 */

import { readFileSync, readdirSync } from 'node:fs'
import { readFile } from 'node:fs/promises'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { BUNDLED_SKILL_RANK } from '@deepseek-ai/dsh-skill'

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

/** @param {import('@deepseek-ai/cordis').Context} ctx */
export function apply(ctx) {
  const candidates = bundledSkillNames().map(skillName => {
    const locator = join(SKILL_ROOT, skillName, 'SKILL.md')
    const { description } = parseSkill(readFileSync(locator, 'utf8'), locator)
    return {
      name: skillName,
      description,
      invocation: { modelInvocable: true, userInvocable: true },
      provider: 'knowwork-bundled',
      source: 'bundled',
      rank: BUNDLED_SKILL_RANK,
      resourceBase: { kind: 'directory', path: RESOURCE_BASE },
      locator,
    }
  })
  const provider = {
    name: 'knowwork-bundled',
    list: () => Promise.resolve(candidates),
    async get(candidate, options) {
      const { rank: _rank, locator, ...summary } = candidate
      const raw = await readFile(locator, { encoding: 'utf8', signal: options?.signal })
      return { ...summary, content: parseSkill(raw, locator).content }
    },
  }
  ctx.skills.registerProvider(() => provider)
}
