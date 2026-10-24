import { test } from 'node:test'
import assert from 'node:assert/strict'
import { startHost, collectEvents, isTurnEnd } from './support/harness.mjs'

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
