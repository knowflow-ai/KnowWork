/**
 * 测试用的业务服务工具接口桩：实现 `GET /list` 与 `POST /execute`，按令牌决定可用工具，记录每次请求。
 * 真实实现在 server/src/knowwork_server/tools/，端到端用例在 server/tests/test_tools_e2e.py。
 */

import { createServer } from 'node:http'

/**
 * @param {{ toolsByToken: Record<string, object[]>, notes?: string, execute?: (call: object) => ({ status?: number, body: object } | undefined | Promise<{ status?: number, body: object } | undefined>) }} options
 */
export async function startToolsStub(options) {
  const requests = []
  const server = createServer(async (req, res) => {
    const chunks = []
    for await (const chunk of req) chunks.push(chunk)
    const body = chunks.length > 0 ? JSON.parse(Buffer.concat(chunks).toString('utf8')) : undefined
    const token = (req.headers.authorization ?? '').replace(/^Bearer /, '')
    const record = { method: req.method, path: req.url, token, idempotencyKey: req.headers['idempotency-key'], body }
    requests.push(record)
    const reply = (status, payload) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(payload)) }
    const tools = options.toolsByToken[token]
    if (tools === undefined) return reply(401, { error: { code: 'unauthorized', message: '令牌无效' } })
    if (req.method === 'GET' && req.url.endsWith('/list')) {
      return reply(200, { tools, ...(options.notes === undefined ? {} : { notes: options.notes }) })
    }
    if (req.method === 'POST' && req.url.endsWith('/execute')) {
      const custom = await options.execute?.(record)
      if (custom !== undefined) return reply(custom.status ?? 200, custom.body)
      return reply(200, {
        actionId: `act-${requests.length}`,
        status: 'succeeded',
        content: `${body.toolName} 结果：${JSON.stringify(body.arguments)}`,
        truncated: false,
        replayed: false,
      })
    }
    return reply(404, { error: { code: 'not_found', message: 'not found' } })
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const { port } = server.address()
  return {
    baseUrl: `http://127.0.0.1:${port}/internal/extensions/tools`,
    requests,
    executions: () => requests.filter(r => r.path.endsWith('/execute')),
    close: () => new Promise(resolve => { server.closeAllConnections?.(); server.close(() => resolve(undefined)) }),
  }
}

/** 一个业务工具描述。 */
export const bizTool = (name, risk = 'read', description = `业务工具 ${name}`) => ({
  name,
  description,
  risk,
  inputSchema: { type: 'object', properties: { q: { type: 'string', description: '参数' } }, required: ['q'] },
})
