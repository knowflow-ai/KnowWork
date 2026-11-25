/**
 * 测试用的最小 OpenAI 兼容端点，扮演业务服务的模型网关。
 * 只实现 POST /v1/chat/completions 的流式响应，按脚本依次返回文字或工具调用，并记录每次请求的头与体。
 * 官方 dsh-llm-mock-server 只讲 Messages 协议，而业务网关对外是 OpenAI 协议，所以这里单独写一个。
 */

import { createServer } from 'node:http'

/**
 * @param {{ script?: Array<{ text: string } | { tool: string, args: object } | { calls: Array<{ tool: string, args: object }> }>, pick?: (body: object) => ({ text: string } | { tool: string, args: object }), token?: () => string }} options
 *   calls 是一步里同时发几个工具调用。
 *   { truncated: { tool, args } } 扮演输出到单次上限被截停：发出一个参数只写了一半的工具调用（args 是半截的 JSON 文本），
 *   结束原因 length，输出 token 记 8192（照 2026-11 客户实例：管理员设的最大输出 8192）。
 *   token 返回当前有效的 API Key；不匹配返回 401。
 *   pick 按请求内容选回答（多个 Agent 并发请求时按到达顺序不确定，用它按角色分别编排）；给了就不用 script。
 *   { refuse: { message, status?, text? } } 扮演业务网关拦下这次调用（个人专属宿主的内容检查，server 的 gateway/content_guard.py）：
 *   给了 status 就不调模型、直接回这个 HTTP 状态和错误；没给就先吐 text、再在流里发一条错误事件后断开。
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
    if ('refuse' in step && step.refuse.status !== undefined) {
      res.writeHead(step.refuse.status, { 'content-type': 'application/json' })
      return res.end(JSON.stringify({ error: { message: step.refuse.message, type: 'content_blocked' } }))
    }
    res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' })
    const id = `chatcmpl-${next}`
    const base = { id, object: 'chat.completion.chunk', created: Math.floor(Date.now() / 1000), model: body?.model ?? 'stub' }
    const send = data => res.write(`data: ${JSON.stringify(data)}\n\n`)
    send({ ...base, choices: [{ index: 0, delta: { role: 'assistant', content: '' }, finish_reason: null }] })
    if ('refuse' in step) {
      for (const piece of (step.refuse.text ?? '').match(/.{1,8}/gu) ?? []) {
        send({ ...base, choices: [{ index: 0, delta: { content: piece }, finish_reason: null }] })
      }
      send({ ...base, choices: [], usage: { prompt_tokens: 10, completion_tokens: 3, total_tokens: 13 } })
      send({ error: { message: step.refuse.message, type: 'content_blocked' } })
      return res.end()
    }
    if ('truncated' in step) {
      const toolCall = { index: 0, id: `call_${next}`, type: 'function', function: { name: step.truncated.tool, arguments: step.truncated.args } }
      send({ ...base, choices: [{ index: 0, delta: { tool_calls: [toolCall] }, finish_reason: null }] })
      send({ ...base, choices: [{ index: 0, delta: {}, finish_reason: 'length' }] })
      send({ ...base, choices: [], usage: { prompt_tokens: 10, completion_tokens: 8192, total_tokens: 8202 } })
      res.write('data: [DONE]\n\n')
      return res.end()
    }
    if ('text' in step) {
      for (const piece of step.text.match(/.{1,8}/gu) ?? []) {
        send({ ...base, choices: [{ index: 0, delta: { content: piece }, finish_reason: null }] })
      }
      send({ ...base, choices: [{ index: 0, delta: {}, finish_reason: 'stop' }] })
    } else {
      const calls = step.calls ?? [{ tool: step.tool, args: step.args }]
      calls.forEach((call, index) => {
        // 单个调用的编号照旧是 call_<第几次请求>（提问等测试按它找），一步多个时才加序号
        const id = step.calls === undefined ? `call_${next}` : `call_${next}_${index}`
        const toolCall = { index, id, type: 'function', function: { name: call.tool, arguments: JSON.stringify(call.args) } }
        send({ ...base, choices: [{ index: 0, delta: { tool_calls: [toolCall] }, finish_reason: null }] })
      })
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
