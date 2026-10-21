/**
 * E1 剩余项 3：长工具执行期间的插话与取消。
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { startHost, collectEvents, isTurnEnd } from './support/harness.mjs'

const sessionEvents = frames => frames.filter(f => f.kind === 'session/event').map(f => f.data)
const messagesText = request => JSON.stringify(request.body?.messages ?? [])
const lastCursor = frames => frames.at(-1)?.cursor ?? 0
const isToolCall = f => f.kind === 'session/event' && f.data?.type === 'tool/call'

test('长工具执行中插话：不打断正在执行的工具，内容在工具结束后的下一次模型请求里生效', async () => {
  const host = await startHost({ sequence: ['tool_call_success', 'success'], toolName: 'kw_sleep', toolArguments: JSON.stringify({ ms: 1500 }) })
  try {
    await host.call('POST', '/v1/sessions', { sessionId: 'lt-steer' })
    await host.call('POST', '/v1/sessions/lt-steer/messages', { text: '睡一会' })
    const started = await collectEvents(host, 'lt-steer', isToolCall)
    const steeredAt = Date.now()
    await host.call('POST', '/v1/sessions/lt-steer/messages', { text: '补充：睡完就停', mode: 'steer' })
    const rest = await collectEvents(host, 'lt-steer', isTurnEnd, { after: lastCursor(started) })
    const events = sessionEvents(rest)
    const result = events.find(e => e.type === 'tool/result')
    assert.ok(result, '没有工具结果')
    assert.ok(JSON.stringify(result).includes('slept 1500'), `工具被打断了：${JSON.stringify(result).slice(0, 300)}`)
    assert.equal(host.llm.requests.length, 2)
    assert.ok(messagesText(host.llm.requests[1]).includes('补充：睡完就停'), '插话没有进入工具结束后的请求')
    assert.ok(Date.now() - steeredAt >= 1000, '插话之后工具没有继续执行完')
  } finally {
    await host.stop()
  }
})

test('长工具执行中取消：工具收到中止信号，取消在 5 秒内停稳，之后可以继续对话', async () => {
  const host = await startHost({ sequence: ['tool_call_success', 'success'], toolName: 'kw_sleep', toolArguments: JSON.stringify({ ms: 60000 }) })
  try {
    await host.call('POST', '/v1/sessions', { sessionId: 'lt-cancel' })
    await host.call('POST', '/v1/sessions/lt-cancel/messages', { text: '睡很久' })
    const started = await collectEvents(host, 'lt-cancel', isToolCall)
    const t0 = Date.now()
    const cancelled = await host.call('POST', '/v1/sessions/lt-cancel/cancel')
    assert.equal(cancelled.body.settled, true, JSON.stringify(cancelled.body))
    assert.ok(Date.now() - t0 < 5000)
    const ended = await collectEvents(host, 'lt-cancel', isTurnEnd, { after: lastCursor(started) })
    const reason = sessionEvents(ended).find(e => e.type === 'turn/end')?.data?.reason?.kind
    assert.equal(reason, 'aborted', `回合结束原因：${reason}`)
    assert.ok(!JSON.stringify(sessionEvents(ended)).includes('slept 60000'), '工具没有被中止')
    await host.call('POST', '/v1/sessions/lt-cancel/messages', { text: '继续' })
    const next = await collectEvents(host, 'lt-cancel', isTurnEnd, { after: lastCursor(ended) })
    assert.equal(sessionEvents(next).find(e => e.type === 'turn/end')?.data?.reason?.kind, 'completed')
  } finally {
    await host.stop()
  }
})
