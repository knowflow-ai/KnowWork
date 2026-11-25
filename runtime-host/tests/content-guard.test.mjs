/**
 * 内容检查（content-guard.mjs）：真实 DSH 组合 + 业务网关桩 + 业务工具桩 + 内容检查接口桩。
 * - 员工的消息命中：不调模型，这一轮以 blocked 结束，会话日志里记一条带上提示与被拒的消息；这条消息不进历史，下一轮照常；
 * - 工具取回的内容命中：工具执行了，但结果换成一句说明再写进历史，不再调模型，这一轮以 CONTENT_BLOCKED 结束；
 * - 回答命中（词跨两块）：不重试、客户端看不到完整的词，以 CONTENT_BLOCKED 结束，话术是管理员写的；
 * - 工具参数命中：工具不执行；
 * - 没开：检查接口一次都不调，流程照旧；检查接口出错：开了检查的不放行，以 CHECK_UNAVAILABLE 结束；
 * - 个人专属宿主里模型网关拦下（调用前回 HTTP 错误，或输出中途发错误事件）：换成同样的结束和话术，不重试。
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { startHost, collectEvents, isTurnEnd } from './support/harness.mjs'
import { startOpenAiStub } from './support/openai-stub.mjs'
import { bizTool, startToolsStub } from './support/tools-stub.mjs'
import { startContentSafetyStub } from './support/content-safety-stub.mjs'

const PRESETS = join(dirname(fileURLToPath(import.meta.url)), 'overlays', 'plugin-presets.yml')

/** 业务服务激活接口的替身：一律同意 */
async function startAllowAll() {
  const server = createServer((_req, res) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end('{"ok":true}') })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  return { baseUrl: `http://127.0.0.1:${server.address().port}/internal/host/v1`, close: () => server.close() }
}

const WORD = '星河计划'
const MESSAGE = '当前对话内容违反公司规范，请修改后重新输入'
const TOKEN = 'guard-tools-token-'.padEnd(40, 'g')

async function startEnv(script, { guard = {}, execute, entry = true, overlays = false } = {}) {
  const gateway = await startOpenAiStub({ script })
  const tools = await startToolsStub({
    toolsByToken: { [TOKEN]: [bizTool('kw_biz_lookup', 'read', '查资料'), bizTool('kw_biz_send', 'read', '把内容发给某人')] },
    execute,
  })
  const safety = await startContentSafetyStub({ token: TOKEN, words: [WORD], message: MESSAGE, ...guard })
  const activation = await startAllowAll()
  const host = await startHost({ sequence: ['success'] }, {
    KNOWWORK_MODEL_PROVIDER: 'knowwork-gateway',
    KNOWWORK_MODEL: 'kw-chat',
    KNOWWORK_MODEL_BASE_URL: gateway.baseURL,
    KNOWWORK_MODEL_TOKEN: 'gw-token-guard-0001-aaaaaaaaaa',
    KNOWWORK_TOOLS_BASE_URL: tools.baseUrl,
    ...entry ? { KNOWWORK_CONTENT_SAFETY_BASE_URL: safety.baseUrl } : {},
    // 长任务目标那条用例：每轮开始前的核对直接放行（激活接口的替身在 goal-rounds.test.mjs 里测）
    ...overlays ? { KNOWWORK_ACTIVATION_BASE_URL: activation.baseUrl } : {},
  }, overlays ? { patches: [PRESETS] } : {})
  const chat = () => gateway.requests.filter(r => r.path.endsWith('/chat/completions'))
  return {
    host, tools, safety, chat,
    stop: async () => { await host.stop(); await gateway.close(); await tools.close(); await safety.close(); activation.close() },
  }
}

async function open(host, sessionId) {
  const opened = await host.call('POST', '/v1/sessions', { sessionId, tools: { token: TOKEN } })
  assert.ok(opened.status < 300, JSON.stringify(opened.body))
}

/** 发一条消息、等这一轮结束，返回这一轮收到的帧。after 是上一轮最后一帧的游标 */
async function ask(host, sessionId, text, after = 0) {
  const sent = await host.call('POST', `/v1/sessions/${sessionId}/messages`, { text })
  assert.equal(sent.status, 202, JSON.stringify(sent.body))
  return collectEvents(host, sessionId, isTurnEnd, { after })
}

const events = frames => frames.filter(f => f.kind === 'session/event').map(f => f.data)
const turnEnd = frames => events(frames).find(e => e.type === 'turn/end')?.data
const lastCursor = frames => frames.positions

test('员工的消息命中：不调模型、这条消息不进历史，下一轮照常', async () => {
  const env = await startEnv([{ text: '好的，已整理' }])
  try {
    await open(env.host, 'guard-input')
    const blocked = await ask(env.host, 'guard-input', `把${WORD}的进度整理一下`)
    assert.equal(env.chat().length, 0, '命中就不调模型')
    assert.equal(turnEnd(blocked)?.reason?.kind, 'blocked')
    const record = events(blocked).find(e => e.type === 'content-safety/blocked')
    assert.equal(record?.data?.turn, turnEnd(blocked)?.turn)
    assert.equal(record?.data?.message, MESSAGE)
    assert.equal(record?.data?.code, 'CONTENT_BLOCKED')
    assert.match(JSON.stringify(record?.data?.messages), new RegExp(WORD), '被拒的消息记在会话日志里，业务服务照普通提问展示')
    assert.ok(!events(blocked).some(e => e.type === 'user/message'), '被拒的消息不进会话历史')

    const next = await ask(env.host, 'guard-input', '整理一下本周的进度', lastCursor(blocked))
    assert.equal(turnEnd(next)?.reason?.kind, 'completed')
    assert.equal(env.chat().length, 1)
    assert.ok(!JSON.stringify(env.chat()[0].body.messages).includes(WORD), '下一轮发给模型的历史里没有被拒的那句')
    assert.deepEqual(env.safety.checks.filter(c => c.direction === 'input').map(c => c.segments),
      [[`把${WORD}的进度整理一下`], ['整理一下本周的进度']])
  } finally {
    await env.stop()
  }
})

test('工具取回的内容命中：结果换成说明再进历史，不再调模型', async () => {
  const env = await startEnv([{ tool: 'kw_biz_lookup', args: { q: '进度' } }, { text: '不该走到这里' }], {
    execute: () => ({ body: { actionId: 'a1', status: 'succeeded', content: `本周${WORD}完成了 80%`, truncated: false, replayed: false } }),
  })
  try {
    await open(env.host, 'guard-tool-result')
    const frames = await ask(env.host, 'guard-tool-result', '查一下进度')
    assert.equal(env.tools.executions().length, 1, '工具照常执行')
    assert.equal(env.chat().length, 1, '结果被拦下以后不再调模型')
    const end = turnEnd(frames)
    assert.equal(end?.reason?.kind, 'error')
    assert.equal(end?.reason?.error?.code, 'CONTENT_BLOCKED')
    assert.equal(end?.reason?.error?.message, MESSAGE)
    const result = events(frames).find(e => e.type === 'tool/result')
    assert.ok(result, '工具结果照样写进历史')
    assert.ok(!JSON.stringify(frames).includes(WORD), '敏感内容不进历史，也不推给客户端')
    assert.equal(env.safety.checks.find(c => c.direction === 'input' && c.tool === 'kw_biz_lookup')?.tool, 'kw_biz_lookup')
  } finally {
    await env.stop()
  }
})

test('回答命中（词跨两块）：不重试、客户端看不到完整的词、带着管理员的话术结束', async () => {
  // 模型桩每 8 个字一块：「一二三四五六七星」「河计划请查收」
  const env = await startEnv([{ text: `一二三四五六七${WORD}请查收` }, { text: '不该走到这里' }])
  try {
    await open(env.host, 'guard-output')
    const frames = await ask(env.host, 'guard-output', '把材料发我')
    assert.equal(env.chat().length, 1, '命中后不重试')
    assert.ok(!JSON.stringify(frames).includes(WORD), '命中前客户端不该收到完整的词')
    const end = turnEnd(frames)
    assert.equal(end?.reason?.error?.code, 'CONTENT_BLOCKED')
    assert.equal(end?.reason?.error?.message, MESSAGE)
    const attempt = events(frames).find(e => e.type === 'assistant/attempt')
    const usage = (attempt?.data?.stream ?? []).map(entry => entry.chunk).find(chunk => chunk?.type === 'usage')
    assert.ok(usage, '用量照常记在这一步的 assistant/attempt 里')
  } finally {
    await env.stop()
  }
})

test('工具参数命中：工具不执行', async () => {
  const env = await startEnv([{ tool: 'kw_biz_send', args: { q: `${WORD}的全部材料` } }, { text: '不该走到这里' }])
  try {
    await open(env.host, 'guard-args')
    const frames = await ask(env.host, 'guard-args', '把材料发给张三')
    assert.equal(env.tools.executions().length, 0)
    assert.equal(turnEnd(frames)?.reason?.error?.code, 'CONTENT_BLOCKED')
    assert.equal(env.safety.checks.find(c => c.direction === 'tool_args')?.tool, 'kw_biz_send')
  } finally {
    await env.stop()
  }
})

test('没开：检查接口一次都不调，回答完整', async () => {
  const env = await startEnv([{ tool: 'kw_biz_lookup', args: { q: WORD } }, { text: `${WORD}进展顺利` }],
    { guard: { input: false, output: false } })
  try {
    await open(env.host, 'guard-off')
    const frames = await ask(env.host, 'guard-off', `${WORD}怎么样了`)
    assert.equal(turnEnd(frames)?.reason?.kind, 'completed')
    assert.equal(env.safety.checks.length, 0)
    assert.equal(env.tools.executions().length, 1)
    const answer = events(frames).filter(e => e.type === 'assistant/message').at(-1)
    assert.match(JSON.stringify(answer?.data?.message?.content), new RegExp(`${WORD}进展顺利`))
  } finally {
    await env.stop()
  }
})

test('只开「检查输出」：消息不查，回答里的词照样拦', async () => {
  const env = await startEnv([{ text: `关于${WORD}，进展顺利` }], { guard: { input: false } })
  try {
    await open(env.host, 'guard-output-only')
    const frames = await ask(env.host, 'guard-output-only', `${WORD}怎么样了`)
    assert.equal(env.chat().length, 1, '消息没查，照常调模型')
    assert.equal(turnEnd(frames)?.reason?.error?.code, 'CONTENT_BLOCKED')
    assert.ok(env.safety.checks.every(c => c.direction !== 'input'))
  } finally {
    await env.stop()
  }
})

test('没给检查接口的地址：不查，也不往别处送令牌', async () => {
  const env = await startEnv([{ text: `${WORD}进展顺利` }], { entry: false })
  try {
    await open(env.host, 'guard-no-entry')
    const frames = await ask(env.host, 'guard-no-entry', `${WORD}怎么样了`)
    assert.equal(turnEnd(frames)?.reason?.kind, 'completed')
    assert.equal(env.safety.checks.length, 0)
  } finally {
    await env.stop()
  }
})

test('检查接口出错：开了检查的不放行', async () => {
  const env = await startEnv([{ text: '你好' }], { guard: { checkStatus: 500 } })
  try {
    await open(env.host, 'guard-down')
    const frames = await ask(env.host, 'guard-down', '你好')
    assert.equal(env.chat().length, 0)
    assert.equal(turnEnd(frames)?.reason?.kind, 'blocked')
    assert.equal(events(frames).find(e => e.type === 'content-safety/blocked')?.data?.code, 'CHECK_UNAVAILABLE')
  } finally {
    await env.stop()
  }
})

for (const [name, refuse, code, guard] of [
  ['调用前回 400（网关查到系统提示里的词）', { status: 400, message: `knowwork-content-blocked: ${MESSAGE}` }, 'CONTENT_BLOCKED', {}],
  ['输出中途发错误事件', { text: '好的，这就', message: `knowwork-content-blocked: ${MESSAGE}` }, 'CONTENT_BLOCKED', {}],
  ['检查接口不通回 503（本来会重试的状态）', { status: 503, message: 'knowwork-check-unavailable: 内容检查暂时不可用，请稍后再试' },
    'CHECK_UNAVAILABLE', {}],
  ['宿主这边没开输出检查', { text: '好的', message: `knowwork-content-blocked: ${MESSAGE}` }, 'CONTENT_BLOCKED', { output: false }],
]) {
  test(`专属宿主里模型网关拦下（${name}）：换成同样的结束和话术，不重试`, async () => {
    const env = await startEnv([{ refuse }, { text: '不该走到这里' }], { guard })
    try {
      await open(env.host, 'guard-gateway')
      const frames = await ask(env.host, 'guard-gateway', '整理一下本周的进度')
      assert.equal(env.chat().length, 1, '不重试')
      const end = turnEnd(frames)
      assert.equal(end?.reason?.kind, 'error')
      assert.equal(end?.reason?.error?.code, code)
      assert.equal(end?.reason?.error?.message, refuse.message.slice(refuse.message.indexOf(':') + 1).trim())
    } finally {
      await env.stop()
    }
  })
}

test('长任务目标：用 /goal 写进去的目标命中，自动接着做的那一轮不调模型，目标标成卡住、写明原因', async () => {
  const env = await startEnv([{ text: '好的' }], { overlays: true })
  try {
    const opened = await env.host.call('POST', '/v1/sessions', { sessionId: 'guard-goal', tools: { token: TOKEN }, plugins: ['goal'],
      owner: { workspaceId: 'ws1', userId: 'u1' } })
    assert.ok(opened.status < 300, JSON.stringify(opened.body))
    await env.host.call('POST', '/v1/sessions/guard-goal/commands', { line: `/goal 把${WORD}的文档都整理一遍` })
    await collectEvents(env.host, 'guard-goal', (_frame, all) =>
      all.some(f => f.kind === 'session/event' && f.data?.type === 'goal/change' && f.data.data?.goal?.phase === 'blocked'),
    { timeoutMs: 30_000 })
    assert.equal(env.chat().length, 0, '命中就不调模型')
    const shown = (await env.host.call('POST', '/v1/sessions/guard-goal/commands', { line: '/goal' })).body.text
    assert.match(shown, /Status: blocked/)
    assert.ok(shown.includes(MESSAGE), '卡住的原因是管理员写的阻断提示')
  } finally {
    await env.stop()
  }
})
