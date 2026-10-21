/**
 * DSH 升级预检：换 DSH 版本之前，确认候选版本下我们装着的每个官方包都已发布在 npm 官方仓库，并且带完整的
 * SHA-512 完整性摘要。只读：不改文件、不安装。通过后再改 package.json、npm install、跑 npm test（准入测试）。
 *
 * 用法：npm run preflight -- 0.2.2
 *
 * 查哪些包：
 * - package-lock.json 里与当前 @deepseek-ai/dsh 同版本的全部官方包（含间接依赖），它们随 DSH 一起发版；
 *   cordis、schemastery、libreoffice-kit 这类有自己版本号的包不按候选版本查；
 * - 配置档与配置层的 cordis.patch.yml 按名字挂上的官方插件，万一不在依赖树里也要查到。
 */

import { readFile } from 'node:fs/promises'
import { join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const ROOT = fileURLToPath(new URL('..', import.meta.url))
const REGISTRY = 'https://registry.npmjs.org'
const PATCH_FILES = ['profile/cordis.patch.yml', 'plugins/host-bundle/cordis.patch.yml']
const LOCKSTEP = /^@deepseek-ai\/dsh(-[a-z0-9-]+)?$/
const VERSION = /^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$/
const PARALLEL = 8

/** 要查的包名（排好序）与当前 DSH 版本。 */
export async function officialPackages(root = ROOT) {
  const lock = JSON.parse(await readFile(join(root, 'package-lock.json'), 'utf8'))
  const current = lock.packages['node_modules/@deepseek-ai/dsh']?.version
  if (current === undefined) throw new Error('package-lock.json 里没有 @deepseek-ai/dsh，先 npm install')
  const names = new Set()
  for (const [path, entry] of Object.entries(lock.packages)) {
    const name = path.slice(path.lastIndexOf('node_modules/') + 'node_modules/'.length)
    if (path.includes('node_modules/') && LOCKSTEP.test(name) && entry.version === current) names.add(name)
  }
  for (const file of PATCH_FILES) {
    const text = await readFile(join(root, file), 'utf8')
    for (const [name] of text.matchAll(/@deepseek-ai\/dsh(?:-[a-z0-9-]+)?/g)) names.add(name)
  }
  return { current, names: [...names].sort() }
}

/** 一个包在仓库里的发布信息有什么问题；没问题返回 null。 */
export function releaseProblem(meta, name, version) {
  if (meta?.name !== name || meta?.version !== version) return '仓库返回的包名或版本对不上'
  let tarball
  try {
    tarball = new URL(meta.dist?.tarball)
  } catch {
    return '没有下载地址'
  }
  if (tarball.origin !== REGISTRY || tarball.username || tarball.password) return `下载地址不在官方仓库：${tarball.origin}`
  const integrity = meta.dist?.integrity ?? ''
  const digest = integrity.startsWith('sha512-') ? Buffer.from(integrity.slice(7), 'base64') : Buffer.alloc(0)
  if (digest.length !== 64) return '缺少完整的 SHA-512 完整性摘要'
  return null
}

/**
 * 逐个查候选版本。fetchJson(url) 返回 { status, body }，测试里换成假的。
 * 结果：published 是查过没问题的包，missing 是仓库里没有这个版本的包，problems 是有问题或没查成的包。
 */
export async function preflight(version, { root = ROOT, fetchJson = fetchFromRegistry } = {}) {
  if (!VERSION.test(version)) throw new Error(`候选版本要写完整的版本号，例如 0.2.2，收到的是 ${version}`)
  const { current, names } = await officialPackages(root)
  const results = new Map()
  const pending = [...names]
  async function next() {
    for (let name = pending.shift(); name !== undefined; name = pending.shift()) {
      try {
        const { status, body } = await fetchJson(`${REGISTRY}/${encodeURIComponent(name)}/${version}`)
        if (status === 404) results.set(name, { kind: 'missing' })
        else if (status !== 200) results.set(name, { kind: 'problem', reason: `仓库返回 ${status}` })
        else {
          const reason = releaseProblem(body, name, version)
          results.set(name, reason === null ? { kind: 'published' } : { kind: 'problem', reason })
        }
      } catch (error) {
        results.set(name, { kind: 'problem', reason: `请求失败：${error.message}` })
      }
    }
  }
  await Promise.all(Array.from({ length: PARALLEL }, next))
  const pick = kind => names.filter(name => results.get(name).kind === kind)
  const problems = pick('problem').map(name => ({ name, reason: results.get(name).reason }))
  return { current, candidate: version, published: pick('published'), missing: pick('missing'), problems,
    ok: pick('published').length === names.length }
}

async function fetchFromRegistry(url) {
  const response = await fetch(url, { signal: AbortSignal.timeout(20_000), headers: { accept: 'application/json' } })
  return { status: response.status, body: response.ok ? await response.json() : null }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const version = process.argv[2]?.replace(/^--version=/, '')
  if (!version) {
    process.stderr.write('用法：npm run preflight -- <候选版本>，例如 npm run preflight -- 0.2.2\n')
    process.exit(2)
  }
  const report = await preflight(version)
  const total = report.published.length + report.missing.length + report.problems.length
  const lines = [`DSH ${report.current} → ${report.candidate}：查了 ${total} 个官方包`]
  if (report.missing.length === total) lines.push(`npm 上还没有发布 ${report.candidate}，一个包都查不到`)
  else if (report.missing.length > 0) {
    const shown = report.missing.slice(0, 10).join('、')
    lines.push(`仓库里还没有这个版本：${shown}${report.missing.length > 10 ? ` 等 ${report.missing.length} 个` : ''}`)
  }
  for (const { name, reason } of report.problems) lines.push(`${name}：${reason}`)
  lines.push(report.ok
    ? '全部已发布。下一步：把 package.json 里所有 DSH 包改到候选版本，npm install，再跑 npm test。'
    : '预检没通过，先不要升级。')
  process.stdout.write(`${lines.join('\n')}\n`)
  if (!report.ok) process.exitCode = 1
}
