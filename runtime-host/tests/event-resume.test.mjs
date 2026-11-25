/**
 * 事件流按来源与官方会话日志的序号续读（http-host.mjs 的 streamEvents）：宿主重启后位置照旧有效，只换实例号；
 * 积压之后给一帧此刻的 Agent 状态；位置参数不合规整个拒绝。
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { startHost, collectEvents, isTurnEnd, isTurnEndAfter } from './support/harness.mjs'

const instanceOf = frames => frames.find(f => f.kind === 'host/instance')?.data?.instance
const isStatus = frame => frame.kind === 'agent/status'
const durable = frames => frames.filter(f => f.epoch !== 'live')

test('宿主重启后从原来的官方序号接着读：已收到的不重发，实例号变了', async () => {
  const first = await startHost({ sequence: ['success'], successText: '第一轮回答' })
  let second
  try {
    await first.call('POST', '/v1/sessions', { sessionId: 'resume-1' })
    await first.call('POST', '/v1/sessions/resume-1/messages', { text: '第一句' })
    const before = await collectEvents(first, 'resume-1', isTurnEnd)
    assert.ok(before.every(f => f.epoch === 'live' || f.epoch === 'host'), '根会话的事件来源是 host')
    const seen = before.positions.get('host')
    assert.ok(Number.isSafeInteger(seen) && seen > 0)
    await first.stop({ keepHome: true })

    second = await startHost({ sequence: ['success'], successText: '重启后的回答' }, {}, { home: first.home })
    const resumed = await second.call('POST', '/v1/sessions', { sessionId: 'resume-1', resume: true, policy: {} })
    assert.equal(resumed.status, 201, JSON.stringify(resumed.body))

    // 接着读：开头是新的实例号，积压里只有重启后的（恢复时官方可能补的），最后是此刻的状态
    const caught = await collectEvents(second, 'resume-1', isStatus, { after: before.positions })
    assert.notEqual(instanceOf(caught), instanceOf(before), '宿主重启后实例号应当变了')
    assert.ok(durable(caught).every(f => f.cursor > seen), '已收到的事件被重发了')
    assert.equal(caught.at(-1).data.status, 'idle')

    // 从头读：历史从磁盘上的会话日志来，重启前那一轮还在
    const all = await collectEvents(second, 'resume-1', isStatus)
    assert.ok(JSON.stringify(all).includes('第一句'), '从头读拿不到重启前的事件')

    await second.call('POST', '/v1/sessions/resume-1/messages', { text: '第二句' })
    const next = await collectEvents(second, 'resume-1', isTurnEndAfter('第二句'), { after: caught.positions })
    assert.ok(durable(next).every(f => f.cursor > seen))
    assert.ok(JSON.stringify(next).includes('重启后的回答'))
  } finally {
    await (second ?? first).stop()
  }
})

test('位置参数不合规整个拒绝', async () => {
  const host = await startHost({ sequence: ['success'] })
  try {
    await host.call('POST', '/v1/sessions', { sessionId: 'resume-bad' })
    for (const after of ['5', 'e1:5', 'host:x', 'device:3', `host:${'a'.repeat(60)}:1`]) {
      const res = await fetch(`${host.base}/v1/sessions/resume-bad/events?after=${encodeURIComponent(after)}`, {
        headers: { authorization: `Bearer ${host.token}` },
      })
      assert.equal(res.status, 400, `after=${after} 应当被拒绝`)
      await res.body?.cancel()
    }
  } finally {
    await host.stop()
  }
})

test('积压合并：同一来源保持官方序号的先后，来源之间按时间穿插（同一来源里时间倒着也不打乱）', async () => {
  const { mergeByTime } = await import('../plugins/dsh-host/src/state.mjs')
  const root = [{ epoch: 'host', cursor: 5, time: 100 }, { epoch: 'host', cursor: 6, time: 99 }, { epoch: 'host', cursor: 7, time: 300 }]
  const child = [{ epoch: 'host:kid', cursor: 0, time: 150 }, { epoch: 'host:kid', cursor: 1, time: 400 }]
  const merged = mergeByTime([root, child])
  assert.deepEqual(merged.map(f => `${f.epoch}:${f.cursor}`), ['host:5', 'host:6', 'host:kid:0', 'host:7', 'host:kid:1'])
  assert.deepEqual(mergeByTime([[], []]), [])
})

test('子会话在来源里的写法：UUID 这类原样用；不合写法的换成摘要，宿主自己发出的来源自己一定认', async () => {
  const { childKeyOf, childIdOfKey, sourceOf } = await import('../plugins/dsh-host/src/state.mjs')
  const uuid = '0f8c2a52-6a7e-4d1f-9a3b-2c1d4e5f6a7b'
  assert.equal(childKeyOf(uuid), uuid)
  assert.equal(childIdOfKey(uuid), uuid)
  for (const odd of ['团队/成员 1', 'x'.repeat(80)]) {
    const key = childKeyOf(odd)
    assert.match(key, /^~[0-9a-f]{40}$/)
    assert.equal(childKeyOf(odd), key, '同一个编号写法固定')
    assert.equal(childIdOfKey(key), undefined)
    assert.ok(`host:${key}`.length <= 64)
  }
  assert.equal(sourceOf({ id: 'root' }, 'root'), 'host')
  assert.equal(sourceOf({ id: 'root' }, '团队/成员 1'), `host:${childKeyOf('团队/成员 1')}`)
})

test('位置参数认摘要写法的子会话来源', async () => {
  const host = await startHost({ sequence: ['success'] })
  try {
    await host.call('POST', '/v1/sessions', { sessionId: 'resume-hash' })
    const after = encodeURIComponent(`host:~${'a'.repeat(40)}:3`)
    const controller = new AbortController()
    const res = await fetch(`${host.base}/v1/sessions/resume-hash/events?after=${after}`, {
      headers: { authorization: `Bearer ${host.token}` }, signal: controller.signal,
    })
    assert.equal(res.status, 200)
    controller.abort()
  } finally {
    await host.stop()
  }
})
