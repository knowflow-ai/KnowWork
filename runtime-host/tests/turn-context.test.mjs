/**
 * 扩展补的每轮上下文（turn-context）：常驻那段照知识库目录的办法发（没变不重发、变了更新、清空了说不再适用、
 * 不带时沿用），只这一轮的那段紧跟在提问后面；不带 context 的会话什么都不多。
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { startHost, collectEvents, isTurnEnd } from './support/harness.mjs'

const STANDING = '你的记忆：回答尽量用表格'
const CHANGED = '你的记忆：回答尽量用表格；在华东区财务组'
const RECALLED = '相关的记忆：A 项目 11 月 15 日截标'
const UPDATED = '以下内容更新了'
const CLEARED = '已经不再适用'

const turnEnds = count => {
  let seen = 0
  return frame => isTurnEnd(frame) && ++seen >= count
}
/** 第 index 次模型请求里的消息原文，按顺序拼起来 */
const messagesOf = (host, index) => JSON.stringify(host.llm.requests[index]?.body?.messages ?? [])
const count = (haystack, needle) => haystack.split(needle).length - 1

async function send(host, sessionId, text, context, turns) {
  const sent = await host.call('POST', `/v1/sessions/${sessionId}/messages`, { text, ...(context === undefined ? {} : { context }) })
  assert.equal(sent.status, 202, JSON.stringify(sent.body))
  await collectEvents(host, sessionId, turnEnds(turns))
}

test('常驻那段：第一轮带上，没变不重发，变了追加新的，清空了说不再适用，不带时沿用', async () => {
  const host = await startHost({ sequence: ['success'], successText: '好' })
  try {
    await host.call('POST', '/v1/sessions', { sessionId: 'tc-standing' })
    await send(host, 'tc-standing', '第一问', { standing: STANDING, turn: '' }, 1)
    assert.equal(count(messagesOf(host, 0), STANDING), 1, '第一轮没有带上常驻那段')

    await send(host, 'tc-standing', '第二问', { standing: STANDING, turn: '' }, 2)
    assert.equal(count(messagesOf(host, 1), STANDING), 1, '内容没变不该再发一遍')

    await send(host, 'tc-standing', '第三问', { standing: CHANGED, turn: '' }, 3)
    const third = messagesOf(host, 2)
    assert.ok(third.includes(CHANGED) && third.lastIndexOf(UPDATED) > third.indexOf('第三问'), '变了要追加一份并写明取代')

    await send(host, 'tc-standing', '第四问', undefined, 4)
    assert.equal(count(messagesOf(host, 3), CHANGED), 1, '这一轮没算时沿用上一轮的，不该重发')

    await send(host, 'tc-standing', '第五问', { standing: '', turn: '' }, 5)
    assert.ok(messagesOf(host, 4).includes(CLEARED), '从有变成空要说一句不再适用')
    await send(host, 'tc-standing', '第六问', { standing: '', turn: '' }, 6)
    assert.equal(count(messagesOf(host, 5), CLEARED), 1, '不再适用只说一次')
  } finally {
    await host.stop()
  }
})

test('只这一轮的那段：紧跟在本轮提问后面，下一轮不再带', async () => {
  const host = await startHost({ sequence: ['success'], successText: '好' })
  try {
    await host.call('POST', '/v1/sessions', { sessionId: 'tc-turn' })
    await send(host, 'tc-turn', '招标那事还剩几天', { standing: '', turn: RECALLED }, 1)
    const first = messagesOf(host, 0)
    assert.ok(first.indexOf(RECALLED) > first.indexOf('招标那事还剩几天'), '要在提问后面')
    assert.ok(!first.includes(CLEARED), '从来没有常驻那段时不该说不再适用')

    await send(host, 'tc-turn', '谢谢', { standing: '', turn: '' }, 2)
    assert.equal(count(messagesOf(host, 1), RECALLED), 1, '上一轮的那段留在历史里，这一轮不再追加')
  } finally {
    await host.stop()
  }
})

test('不带 context 的会话什么都不多；格式不对的 context 被拒', async () => {
  const host = await startHost({ sequence: ['success'], successText: '好' })
  try {
    await host.call('POST', '/v1/sessions', { sessionId: 'tc-none' })
    await send(host, 'tc-none', '你好', undefined, 1)
    assert.ok(!messagesOf(host, 0).includes('knowwork-turn-context') && !messagesOf(host, 0).includes(CLEARED))
    const bad = await host.call('POST', '/v1/sessions/tc-none/messages', { text: '再问', context: { standing: 1 } })
    assert.equal(bad.status, 400)
  } finally {
    await host.stop()
  }
})
