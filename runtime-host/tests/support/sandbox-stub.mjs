/**
 * 业务服务沙箱内部接口的测试桩（/internal/extensions/sandbox/{exec,stat,read,list,write}）。
 * 每个令牌对应一个会话；`/sessions/<会话>` 对到一个本地临时目录（扮演容器的 /workspace），exec 真的在那里跑。
 * 只为验证宿主侧的远程执行器接线，容器本身的行为由业务服务的测试负责。
 */

import { createServer } from 'node:http'
import { execFile } from 'node:child_process'
import { lstatSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, posix } from 'node:path'

export async function startSandboxStub({ sessionsByToken }) {
  const workspaces = new Map()
  const requests = []
  const workspaceOf = sessionId => {
    if (!workspaces.has(sessionId)) workspaces.set(sessionId, mkdtempSync(join(tmpdir(), 'kw-sbx-')))
    return workspaces.get(sessionId)
  }
  const local = (sessionId, path) => {
    const root = `/sessions/${sessionId}`
    const normal = posix.normalize(path)
    if (normal !== root && !normal.startsWith(`${root}/`)) return undefined
    return join(workspaceOf(sessionId), normal.slice(root.length))
  }
  const statOf = (file, follow) => {
    let info
    try {
      info = follow ? statSync(file) : lstatSync(file)
    } catch {
      return null
    }
    const kind = info.isSymbolicLink() ? 'l' : info.isFile() ? 'f' : info.isDirectory() ? 'd' : 'o'
    return { kind, size: info.size, version: `${info.mtimeMs}:${info.ino}:${info.size}` }
  }

  const handlers = {
    exec: (session, body) => new Promise(resolve => {
      const cwd = local(session, body.cwd)
      mkdirSync(cwd, { recursive: true })
      execFile(body.argv[0], body.argv.slice(1), { cwd, env: { PATH: process.env.PATH, ...body.env } }, (error, stdout, stderr) => {
        resolve({ exitCode: error ? (typeof error.code === 'number' ? error.code : 1) : 0, stdout, stderr, timedOut: false, truncated: false })
      })
    }),
    stat: (session, body) => ({ stat: statOf(local(session, body.path), body.follow !== false) }),
    read: (session, body) => {
      const data = readFileSync(local(session, body.path))
      return { size: data.length, data: data.subarray(body.offset, body.offset + body.length).toString('base64') }
    },
    list: (session, body) => ({
      entries: readdirSync(local(session, body.path)).sort().map(name => {
        const stat = statOf(join(local(session, body.path), name), false)
        return { name, kind: stat.kind, size: stat.size }
      }),
    }),
    write: (session, body) => {
      const file = local(session, body.path)
      mkdirSync(dirname(file), { recursive: true })
      writeFileSync(file, Buffer.from(body.data, 'base64'))
      return { stat: statOf(file, true) }
    },
  }

  const server = createServer(async (req, res) => {
    let raw = ''
    for await (const chunk of req) raw += chunk
    const body = JSON.parse(raw || '{}')
    const operation = req.url.split('/').pop()
    const token = (req.headers.authorization ?? '').replace(/^Bearer /, '')
    const session = sessionsByToken[token]
    requests.push({ operation, token, body })
    const reply = (status, payload) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(payload)) }
    if (session === undefined) return reply(401, { error: { code: 'unauthorized', message: '令牌无效' } })
    if (body.path !== undefined && local(session, body.path) === undefined) {
      return reply(403, { error: { code: 'permission_denied', message: '只能访问本会话工作目录下的路径' } })
    }
    try {
      reply(200, await handlers[operation](session, body))
    } catch (error) {
      reply(404, { error: { code: 'not_found', message: String(error.message) } })
    }
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  return {
    baseUrl: `http://127.0.0.1:${server.address().port}/internal/extensions/sandbox`,
    requests,
    workspaceOf,
    close: () => new Promise(resolve => server.close(resolve)),
  }
}
