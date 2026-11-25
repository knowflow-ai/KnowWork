/**
 * 测试用的业务服务内容检查接口桩：`GET /policy` 与 `POST /check`，按子串查词，记录每次检查送来的文字。
 * 真实实现在 server/src/knowwork_server/content_safety/（AC 自动机、审计）。
 */

import { createServer } from 'node:http'

/**
 * @param {{ token: string, words: string[], input?: boolean, output?: boolean, message?: string,
 *   checkStatus?: number }} options checkStatus：/check 回这个状态码（模拟检查服务出错）
 */
export async function startContentSafetyStub(options) {
  const checks = []
  const message = options.message ?? '当前对话内容违反公司规范'
  const input = options.input ?? true
  const output = options.output ?? true
  const enabled = (input || output) && options.words.length > 0
  const server = createServer(async (req, res) => {
    const chunks = []
    for await (const chunk of req) chunks.push(chunk)
    const body = chunks.length > 0 ? JSON.parse(Buffer.concat(chunks).toString('utf8')) : undefined
    const reply = (status, payload) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(payload)) }
    if (req.headers.authorization !== `Bearer ${options.token}`) {
      return reply(401, { error: { code: 'unauthorized', message: '令牌无效' } })
    }
    if (req.method === 'GET' && req.url.endsWith('/policy')) {
      const holdback = Math.max(1, ...options.words.map(word => [...word].length)) - 1
      return reply(200, { enabled, input: enabled && input, output: enabled && output, holdback, version: 1 })
    }
    if (req.method === 'POST' && req.url.endsWith('/check')) {
      checks.push(body)
      if (options.checkStatus !== undefined) return reply(options.checkStatus, { error: { code: 'internal', message: '出错了' } })
      const on = body.direction === 'input' ? input : output
      const hit = on && body.segments.some(segment => options.words.some(word => segment.includes(word)))
      return reply(200, hit ? { blocked: true, message } : { blocked: false })
    }
    return reply(404, { error: { code: 'not_found', message: 'not found' } })
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const { port } = server.address()
  return {
    baseUrl: `http://127.0.0.1:${port}/internal/extensions/content-safety`,
    checks,
    close: () => new Promise(resolve => { server.closeAllConnections?.(); server.close(() => resolve(undefined)) }),
  }
}
