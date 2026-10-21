/**
 * 测试用的业务服务技能接口桩：实现 `GET /catalog` 与 `GET /content`，按令牌决定清单，记录每次请求。
 * 真实实现在 server/src/knowwork_server/skills/internal_api.py。
 */

import { createHash } from 'node:crypto'
import { createServer } from 'node:http'

const sha256 = text => createHash('sha256').update(text, 'utf8').digest('hex')

/**
 * 一个已发布技能。body 是 SKILL.md 正文；摘要按正文算，和业务服务一样。
 * @returns {{ entry: object, body: string }}
 */
export function publishedSkill(name, { body = `技能 ${name} 的正文`, unlocks = [], completionTools = [], allowedTools } = {}) {
  return {
    body,
    entry: {
      name,
      description: `技能 ${name} 的说明`,
      version: '1.0.0',
      digest: sha256(`archive:${name}:${body}`),
      contentDigest: sha256(body),
      allowedTools: allowedTools ?? [...unlocks, ...completionTools.filter(t => !unlocks.includes(t))],
      completionTools,
      unlocks,
      modelInvocable: true,
      userInvocable: true,
      scope: 'workspace',
    },
  }
}

/**
 * @param {{ skillsByToken: Record<string, Array<{ entry: object, body: string }>>, tamper?: boolean }} options
 *   tamper 为真时内容接口返回与摘要不符的正文。
 */
export async function startSkillsStub(options) {
  const requests = []
  const server = createServer((req, res) => {
    const url = new URL(req.url, 'http://stub')
    const token = (req.headers.authorization ?? '').replace(/^Bearer /, '')
    requests.push({ method: req.method, path: url.pathname, query: Object.fromEntries(url.searchParams), token })
    const reply = (status, payload) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(payload)) }
    const skills = options.skillsByToken[token]
    if (skills === undefined) return reply(401, { error: { code: 'unauthorized', message: '令牌无效' } })
    if (url.pathname.endsWith('/catalog')) return reply(200, { skills: skills.map(s => s.entry) })
    if (url.pathname.endsWith('/content')) {
      const found = skills.find(s => s.entry.name === url.searchParams.get('name') && s.entry.digest === url.searchParams.get('digest'))
      if (found === undefined) return reply(404, { error: { code: 'skill_not_found', message: '技能不在本会话的可用范围内' } })
      const { name, version, digest, contentDigest } = found.entry
      return reply(200, { name, version, digest, contentDigest, content: options.tamper ? `${found.body}（被篡改）` : found.body })
    }
    return reply(404, { error: { code: 'not_found', message: 'not found' } })
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const { port } = server.address()
  return {
    baseUrl: `http://127.0.0.1:${port}/internal/extensions/skills`,
    requests,
    contentLoads: () => requests.filter(r => r.path.endsWith('/content')),
    close: () => new Promise(resolve => { server.closeAllConnections?.(); server.close(() => resolve(undefined)) }),
  }
}
