import { test } from 'node:test'
import assert from 'node:assert/strict'
import { liveFrame } from '../plugins/dsh-host/src/http-host.mjs'
import { startHost, collectEvents, isTurnEnd } from './support/harness.mjs'
import { startOpenAiStub } from './support/openai-stub.mjs'
import { startSandboxStub } from './support/sandbox-stub.mjs'
import { startToolsStub } from './support/tools-stub.mjs'

// 逐字输出：官方 dsh-agent-loop 发 agent/assistant-stream（官方会话接口、命令行运行都靠它出字），
// 宿主把正文片段作为临时帧推给当前订阅方，不进回放缓冲；这一步的正式消息照常落成会话事件。
test('回答边生成边推正文片段，拼起来就是正式消息；重新订阅回放时没有这些片段', async () => {
  const text = '短剧行业二〇二四年规模继续增长，头部平台集中度提高。'
  const host = await startHost({ sequence: ['success'], successText: text, chunkSize: 3, chunkDelayMs: 5 })
  try {
    await host.call('POST', '/v1/sessions', { sessionId: 'live-1' })
    await host.call('POST', '/v1/sessions/live-1/messages', { text: '你好' })
    const frames = await collectEvents(host, 'live-1', isTurnEnd)
    const live = frames.filter(f => f.kind === 'assistant/stream').map(f => f.data)
    const pieces = live.filter(d => d.type === 'text')
    assert.ok(pieces.length > 3, `片段太少：${JSON.stringify(live)}`)
    assert.equal(pieces.map(d => d.text).join(''), text)
    const start = live.find(d => d.type === 'start')
    assert.ok(start && start.turn === 1 && Number.isInteger(start.step) && start.attemptId, JSON.stringify(start))
    assert.ok(pieces.every(d => d.attemptId === start.attemptId))
    const message = frames.findIndex(f => f.kind === 'session/event' && f.data.type === 'assistant/message')
    const firstPiece = frames.findIndex(f => f.kind === 'assistant/stream' && f.data.type === 'text')
    assert.ok(firstPiece >= 0 && firstPiece < message, '片段先于正式消息到达')

    const replay = await collectEvents(host, 'live-1', isTurnEnd)
    assert.ok(!replay.some(f => f.kind === 'assistant/stream'), '临时帧不进回放缓冲')
  } finally {
    await host.stop()
  }
})

test('开始写工具调用时报一次工具名（参数片段不转），页面不用一直写「思考中」', () => {
  const announced = new Map()
  const delta = (index, name, argumentsDelta) => ({ type: 'chunk', attemptId: 'a1', chunk: { type: 'tool-call-delta', index, name, argumentsDelta } })
  assert.deepEqual(liveFrame(delta(0, 'write', '{"pa'), announced), { type: 'tool', attemptId: 'a1', name: 'write' })
  assert.equal(liveFrame(delta(0, 'write', 'th":'), announced), undefined, '同一个调用只报一次')
  assert.deepEqual(liveFrame(delta(1, 'present', '{'), announced), { type: 'tool', attemptId: 'a1', name: 'present' })
  assert.equal(liveFrame({ type: 'chunk', attemptId: 'a1', chunk: { type: 'reasoning-delta', text: '想' } }, announced), undefined)
  assert.deepEqual(liveFrame({ type: 'end', attemptId: 'a1' }, announced), { type: 'end', attemptId: 'a1' })
  assert.equal(announced.size, 0, '这次输出结束就忘掉')
})

test('真实宿主：模型调工具之前先推一帧工具名，早于正式的工具调用事件', async () => {
  const TOKEN = 'live-tool-token-'.padEnd(40, 't')
  const gateway = await startOpenAiStub({ script: [
    { tool: 'write', args: { path: '/sessions/live-2/a.html', content: '<p>你好</p>' } },
    { text: '写好了' },
  ] })
  const tools = await startToolsStub({ toolsByToken: { [TOKEN]: [] } })
  const sandbox = await startSandboxStub({ sessionsByToken: { [TOKEN]: 'live-2' } })
  const host = await startHost({ sequence: ['success'] }, {
    KNOWWORK_MODEL_PROVIDER: 'knowwork-gateway', KNOWWORK_MODEL: 'kw-chat', KNOWWORK_MODEL_BASE_URL: gateway.baseURL,
    KNOWWORK_MODEL_TOKEN: 'gw-token-live-0001-aaaaaaaaaa', KNOWWORK_TOOLS_BASE_URL: tools.baseUrl,
    KNOWWORK_SANDBOX_BASE_URL: sandbox.baseUrl,
  })
  try {
    await host.call('POST', '/v1/sessions', { sessionId: 'live-2', tools: { token: TOKEN } })
    await host.call('POST', '/v1/sessions/live-2/messages', { text: '写个网页' })
    const frames = await collectEvents(host, 'live-2', isTurnEnd)
    const named = frames.findIndex(f => f.kind === 'assistant/stream' && f.data.type === 'tool' && f.data.name === 'write')
    const call = frames.findIndex(f => f.kind === 'session/event' && f.data.type === 'tool/call')
    assert.ok(named >= 0 && named < call, JSON.stringify(frames.filter(f => f.kind === 'assistant/stream').map(f => f.data)))
    assert.equal(frames.filter(f => f.kind === 'assistant/stream' && f.data.type === 'tool').length, 1)
  } finally {
    await host.stop()
    await gateway.close()
    await tools.close()
    await sandbox.close()
  }
})
