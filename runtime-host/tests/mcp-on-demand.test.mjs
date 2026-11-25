/**
 * MCP 工具按需加载（mcp-on-demand.mjs，照官方 dsh-mcp-client 的 toolLoading: on-demand）：按需服务的工具不随请求发给模型，
 * 系统提示里列名字与描述第一行；模型用 mcp__<服务名>__load_tools 加载后，下一次请求才带上它、能调用。
 * 真实 DSH 组合 + 可编排的模型网关替身；业务服务由 tools-stub 扮演。
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { startHost, collectEvents, isTurnEnd } from './support/harness.mjs'
import { startOpenAiStub } from './support/openai-stub.mjs'
import { bizTool, startToolsStub } from './support/tools-stub.mjs'

const TOKEN = 'on-demand-tools-token-'.padEnd(40, 'o')
const LOADER = 'mcp__docs__load_tools'
const onDemand = (name, description) => ({ ...bizTool(name, 'read', description), onDemand: { server: 'docs' } })
const TOOLS = [
  bizTool('kw_biz_a'),
  onDemand('mcp__docs__search', '搜文档'),
  onDemand('mcp__docs__fetch', '取文档全文\n第二行不进目录'),
]
const names = request => (request.body.tools ?? []).map(tool => tool.function?.name)
const promptOf = request => JSON.stringify(request.body.messages ?? [])
const chat = gateway => gateway.requests.filter(r => r.path.endsWith('/chat/completions'))

async function boot(script, tools = TOOLS) {
  const gateway = await startOpenAiStub({ script })
  const stub = await startToolsStub({ toolsByToken: { [TOKEN]: tools } })
  const host = await startHost({ sequence: ['success'] }, {
    KNOWWORK_MODEL_PROVIDER: 'knowwork-gateway', KNOWWORK_MODEL: 'kw-chat',
    KNOWWORK_MODEL_BASE_URL: gateway.baseURL, KNOWWORK_MODEL_TOKEN: 'gw-token-on-demand-0001-aaaaaaa',
    KNOWWORK_TOOLS_BASE_URL: stub.baseUrl,
  })
  return { gateway, stub, host, stop: async () => { await host.stop(); await stub.close(); await gateway.close() } }
}

async function turn(env, sessionId, text, extra = {}) {
  const opened = await env.host.call('POST', '/v1/sessions', { sessionId, tools: { token: TOKEN }, ...extra })
  assert.equal(opened.status, 201, JSON.stringify(opened.body))
  await env.host.call('POST', `/v1/sessions/${sessionId}/messages`, { text })
  return collectEvents(env.host, sessionId, isTurnEnd, { timeoutMs: 60_000 })
}

test('按需服务的工具先不给模型，系统提示里列目录；加载后下一次请求才带上，能调用', async () => {
  const env = await boot([
    { tool: LOADER, args: { tools: ['mcp__docs__search'] } },
    { tool: 'mcp__docs__search', args: { q: '采购合同' } },
    { text: '查到了' },
  ])
  try {
    await turn(env, 'od-1', '查一下采购合同')
    const [first, second, third] = chat(env.gateway)
    assert.ok(names(first).includes('kw_biz_a'), '普通业务工具照常给')
    assert.ok(names(first).includes(LOADER), '按需服务给了加载器')
    assert.ok(!names(first).some(name => name === 'mcp__docs__search' || name === 'mcp__docs__fetch'), '按需工具不该先给')
    const prompt = promptOf(first)
    assert.match(prompt, /### On-demand tools from MCP server docs/)
    assert.match(prompt, /- mcp__docs__fetch: 取文档全文/)
    assert.match(prompt, /- mcp__docs__search: 搜文档/)
    assert.ok(!prompt.includes('第二行不进目录'), '目录只放描述的第一行')
    assert.match(promptOf(second), /Loaded: mcp__docs__search\./)
    assert.ok(names(second).includes('mcp__docs__search'), '加载后下一次请求带上了')
    assert.ok(!names(second).includes('mcp__docs__fetch'), '没加载的照旧不给')
    assert.ok(names(third).includes('mcp__docs__search'))
    assert.deepEqual(env.stub.executions().map(call => call.body.toolName), ['mcp__docs__search'])
  } finally {
    await env.stop()
  }
})

test('加载目录里没有的工具：报给模型用目录里的名字，什么也不加', async () => {
  const env = await boot([
    { tool: LOADER, args: { tools: ['mcp__docs__search', 'mcp__docs__nope'] } },
    { text: '好的' },
  ])
  try {
    await turn(env, 'od-2', '查一下')
    const [, second] = chat(env.gateway)
    assert.match(promptOf(second), /MCP server docs does not offer \\"mcp__docs__nope\\"; use names from its on-demand tool list/)
    assert.ok(!names(second).includes('mcp__docs__search'), '有一个不认识就整批不加')
  } finally {
    await env.stop()
  }
})

test('没有按需服务时不出现加载器与目录；按需标记不合法时会话打不开（失败关闭）', async () => {
  const plain = await boot([{ text: '好的' }], [bizTool('kw_biz_a'), bizTool('mcp__docs__search')])
  try {
    await turn(plain, 'od-3', '你好')
    const [request] = chat(plain.gateway)
    assert.ok(!names(request).some(name => name.endsWith('__load_tools')))
    assert.ok(!promptOf(request).includes('On-demand tools'))
  } finally {
    await plain.stop()
  }
  const broken = await boot([{ text: '好的' }], [{ ...bizTool('mcp__x__a'), onDemand: { server: 'Bad_Name' } }])
  try {
    const opened = await broken.host.call('POST', '/v1/sessions', { sessionId: 'od-4', tools: { token: TOKEN } })
    assert.equal(opened.status, 400, '按需标记不合法却打开了会话')
  } finally {
    await broken.stop()
  }
})

test('会话策略禁用的按需工具不进目录、加载不了；全被禁用的服务连加载器也没有', async () => {
  const env = await boot([
    { tool: LOADER, args: { tools: ['mcp__docs__fetch'] } },
    { text: '好的' },
  ])
  try {
    await turn(env, 'od-5', '取全文', { policy: { deny: ['mcp__docs__fetch'] } })
    const [first, second] = chat(env.gateway)
    assert.match(promptOf(first), /- mcp__docs__search: 搜文档/)
    assert.ok(!promptOf(first).includes('mcp__docs__fetch'), '被禁用的工具进了目录')
    assert.match(promptOf(second), /does not offer \\"mcp__docs__fetch\\"/)
    assert.ok(!names(second).includes('mcp__docs__fetch'))
  } finally {
    await env.stop()
  }
  const all = await boot([{ text: '好的' }])
  try {
    await turn(all, 'od-6', '你好', { policy: { deny: ['mcp__docs__search', 'mcp__docs__fetch'] } })
    const [request] = chat(all.gateway)
    assert.ok(!names(request).includes(LOADER))
    assert.ok(!promptOf(request).includes('On-demand tools'))
  } finally {
    await all.stop()
  }
})
