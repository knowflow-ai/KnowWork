/**
 * E1 剩余项 2：同一运行时内多个会话（代表不同用户）的隔离。
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { startHost, collectEvents, isTurnEnd } from './support/harness.mjs'

const sessionEvents = frames => frames.filter(f => f.kind === 'session/event').map(f => f.data)
const messagesText = request => JSON.stringify(request.body?.messages ?? [])

test('被收窄的工具即使被模型凭空调用也不会执行（官方 restrict 已同时阻止执行；guard 是第二层，本用例不单独证明它）', async () => {
  const host = await startHost({ sequence: ['tool_call_success', 'success'], toolName: 'kw_echo', toolArguments: JSON.stringify({ text: '越权' }) })
  try {
    await host.call('POST', '/v1/sessions', { sessionId: 'deny', policy: { deny: ['kw_echo'] } })
    await host.call('POST', '/v1/sessions/deny/messages', { text: '试试调用 kw_echo' })
    await collectEvents(host, 'deny', isTurnEnd)
    assert.equal(host.llm.requests.length, 2)
    assert.ok(!(host.llm.requests[0].body.tools ?? []).some(t => t.name === 'kw_echo'), '模型请求里仍有 kw_echo')
    assert.ok(!messagesText(host.llm.requests[1]).includes('echo: 越权'), '被收窄的工具被执行了')
  } finally {
    await host.stop()
  }
})

test('两个会话同时等待审批：各自只看得到、只能决定自己的审批', async () => {
  const host = await startHost({ sequence: ['tool_call_success', 'tool_call_success', 'success'], toolName: 'kw_write', toolArguments: JSON.stringify({ text: 'x' }) })
  try {
    await host.call('POST', '/v1/sessions', { sessionId: 'user-a' })
    await host.call('POST', '/v1/sessions', { sessionId: 'user-b' })
    await host.call('POST', '/v1/sessions/user-a/messages', { text: 'A 写入' })
    await host.call('POST', '/v1/sessions/user-b/messages', { text: 'B 写入' })
    await Promise.all([
      collectEvents(host, 'user-a', f => f.kind === 'approval/requested'),
      collectEvents(host, 'user-b', f => f.kind === 'approval/requested'),
    ])
    const a = (await host.call('GET', '/v1/sessions/user-a/approvals')).body
    const b = (await host.call('GET', '/v1/sessions/user-b/approvals')).body
    assert.equal(a.length, 1)
    assert.equal(b.length, 1)
    assert.notEqual(a[0].id, b[0].id)
    const crossed = await host.call('POST', `/v1/sessions/user-b/approvals/${a[0].id}`, { outcome: 'allowed-once' })
    assert.equal(crossed.status, 404, '用 B 的会话决定了 A 的审批')
    assert.equal((await host.call('GET', '/v1/sessions/user-a/approvals')).body.length, 1, 'A 的审批被别的会话动了')
    await host.call('POST', `/v1/sessions/user-a/approvals/${a[0].id}`, { outcome: 'allowed-once' })
    await host.call('POST', `/v1/sessions/user-b/approvals/${b[0].id}`, { outcome: 'rejected' })
    const [fa, fb] = await Promise.all([collectEvents(host, 'user-a', isTurnEnd), collectEvents(host, 'user-b', isTurnEnd)])
    assert.equal(fa.find(f => f.kind === 'approval/resolved')?.data.outcome, 'allowed-once')
    assert.equal(fb.find(f => f.kind === 'approval/resolved')?.data.outcome, 'rejected')
  } finally {
    await host.stop()
  }
})

test('事件流与技能门互不串扰：A 有技能门、B 没有；A 的流里看不到 B 的内容', async () => {
  const host = await startHost({ sequence: ['success'], successText: '统一的模拟回答' })
  try {
    await host.call('POST', '/v1/sessions', { sessionId: 'gate-a', policy: { gate: { requiredTool: 'kw_finalize', maxNudges: 1 } } })
    await host.call('POST', '/v1/sessions', { sessionId: 'plain-b' })
    await Promise.all([
      host.call('POST', '/v1/sessions/gate-a/messages', { text: '只属于 A 的问题' }),
      host.call('POST', '/v1/sessions/plain-b/messages', { text: '只属于 B 的问题' }),
    ])
    const [fa, fb] = await Promise.all([collectEvents(host, 'gate-a', isTurnEnd), collectEvents(host, 'plain-b', isTurnEnd)])
    assert.ok(fa.some(f => f.kind === 'skill-gate/nudged'), 'A 没有触发技能门')
    assert.ok(!fb.some(f => f.kind.startsWith('skill-gate/')), 'B 被技能门影响了')
    assert.ok(!JSON.stringify(sessionEvents(fa)).includes('只属于 B 的问题'), 'A 的事件流里出现了 B 的内容')
    assert.ok(!JSON.stringify(sessionEvents(fb)).includes('只属于 A 的问题'), 'B 的事件流里出现了 A 的内容')
  } finally {
    await host.stop()
  }
})
