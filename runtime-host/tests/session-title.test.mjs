// 对话标题以官方为准：官方按第一句话给兜底标题、再用模型起短标题，都写进会话日志的 session/title（业务服务据此同步）；
// 用户在我们页面上改名经 PUT /v1/sessions/:id/title 照官方改名写进去（来源是用户）。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { startHost, collectEvents, isTurnEnd } from './support/harness.mjs'

const titlesOf = frames => frames
  .filter(f => f.kind === 'session/event' && f.data?.type === 'session/title')
  .map(f => f.data.data)

test('第一句话之后有官方标题；用户改名写进官方标题，不合规的拒绝，宿主上没有的会话报不存在', { timeout: 120_000 }, async () => {
  const host = await startHost({ sequence: ['success'], successText: '好的' })
  try {
    assert.equal((await host.call('POST', '/v1/sessions', { sessionId: 'st-1' })).status, 201)
    await host.call('POST', '/v1/sessions/st-1/messages', { text: '帮我整理一下这个季度的差旅报销' })
    const frames = await collectEvents(host, 'st-1', isTurnEnd)
    assert.ok(titlesOf(frames).length > 0, '第一句话之后官方给了标题')

    const renamed = await host.call('PUT', '/v1/sessions/st-1/title', { title: '三季度差旅' })
    assert.equal(renamed.status, 200, JSON.stringify(renamed.body))
    assert.equal(renamed.body.title, '三季度差旅')
    const after = await collectEvents(host, 'st-1', frame => titlesOf([frame]).some(t => t.title === '三季度差旅'),
      { after: frames.positions })
    assert.equal(titlesOf(after).at(-1).title, '三季度差旅')

    assert.equal((await host.call('PUT', '/v1/sessions/st-1/title', { title: '  ' })).status, 400)
    assert.equal((await host.call('PUT', '/v1/sessions/st-gone/title', { title: 'x' })).status, 404)
  } finally {
    await host.stop()
  }
})
