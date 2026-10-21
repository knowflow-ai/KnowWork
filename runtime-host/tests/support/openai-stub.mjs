/**
 * 测试用的最小 OpenAI 兼容端点，扮演业务服务的模型网关。
 * 只实现 POST /v1/chat/completions 的流式响应，按脚本依次返回文字或工具调用，并记录每次请求的头与体。
 * 官方 dsh-llm-mock-server 只讲 Messages 协议，而业务网关对外是 OpenAI 协议，所以这里单独写一个。
 */

import { createServer } from 'node:http'

/**
 * @param {{ script?: Array<{ text: string } | { tool: string, args: object }>, pick?: (body: object) => ({ text: string } | { tool: string, args: object }), token?: () => string }} options
 *   token 返回当前有效的 API Key；不匹配返回 401。
 *   pick 按请求内容选回答（多个 Agent 并发请求时按到达顺序不确定，用它按角色分别编排）；给了就不用 script。
 */
export async function startOpenAiStub(options) {
  const requests = []
  let next = 0
  const server = createServer(async (req, res) => {
    const chunks = []
    for await (const chunk of req) chunks.push(chunk)
    const body = chunks.length > 0 ? JSON.parse(Buffer.concat(chunks).toString('utf8')) : undefined
    const record = { method: req.method, path: req.url, headers: { ...req.headers }, body }
    requests.push(record)
    if (req.method !== 'POST' || !req.url.endsWith('/chat/completions')) {
      res.writeHead(404, { 'content-type': 'application/json' })
      return res.end(JSON.stringify({ error: { message: 'not found' } }))
    }
    const expected = options.token?.()
    if (expected !== undefined && req.headers.authorization !== `Bearer ${expected}`) {
      record.rejected = true
      res.writeHead(401, { 'content-type': 'application/json' })
      return res.end(JSON.stringify({ error: { message: 'invalid token', type: 'authentication_error' } }))
    }
    const step = options.pick?.(body) ?? options.script[Math.min(next, options.script.length - 1)]
    next += 1
    res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' })
    const id = `chatcmpl-${next}`
    const base = { id, object: 'chat.completion.chunk', created: Math.floor(Date.now() / 1000), model: body?.model ?? 'stub' }
    const send = data => res.write(`data: ${JSON.stringify(data)}\n\n`)
    send({ ...base, choices: [{ index: 0, delta: { role: 'assistant', content: '' }, finish_reason: null }] })
    if ('text' in step) {
      for (const piece of step.text.match(/.{1,8}/gu) ?? []) {
        send({ ...base, choices: [{ index: 0, delta: { content: piece }, finish_reason: null }] })
      }
      send({ ...base, choices: [{ index: 0, delta: {}, finish_reason: 'stop' }] })
    } else {
      send({ ...base, choices: [{ index: 0, delta: { tool_calls: [{ index: 0, id: `call_${next}`, type: 'function', function: { name: step.tool, arguments: JSON.stringify(step.args) } }] }, finish_reason: null }] })
      send({ ...base, choices: [{ index: 0, delta: {}, finish_reason: 'tool_calls' }] })
    }
    send({ ...base, choices: [], usage: { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 } })
    res.write('data: [DONE]\n\n')
    res.end()
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const { port } = server.address()
  return {
    baseURL: `http://127.0.0.1:${port}/v1`,
    requests,
    close: () => new Promise(resolve => { server.closeAllConnections?.(); server.close(() => resolve(undefined)) }),
  }
}
