/**
 * 一轮怎么收尾：输出到单次上限被截断后，下一轮告诉模型截在哪、要拆小（output-limit）；
 * 正常结束前待办还有没勾的，让模型把待办更新掉（todo-check）。
 * 真实 DSH 组合，模型由 openai-stub 按脚本扮演；断言落到模型请求与会话事件上。
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { startHost, collectEvents, isTurnEnd } from './support/harness.mjs'
import { startOpenAiStub } from './support/openai-stub.mjs'

const LIMIT_MARK = '达到了单次输出的上限'
const TODO_MARK = '待办里还有没标完成的'

async function startEnv(script) {
  const gateway = await startOpenAiStub({ script })
  const host = await startHost({ sequence: ['success'] }, {
    KNOWWORK_MODEL_PROVIDER: 'knowwork-gateway',
    KNOWWORK_MODEL: 'kw-chat',
    KNOWWORK_MODEL_BASE_URL: gateway.baseURL,
    KNOWWORK_MODEL_TOKEN: 'gw-token-endings-0001-aaaaaaa',
  })
  const chat = () => gateway.requests.filter(r => r.path.endsWith('/chat/completions'))
  return { host, chat, stop: async () => { await host.stop(); await gateway.close() } }
}

/** 发一条消息、等第 turn 轮结束，返回那一轮的 turn/end 事件。 */
async function ask(host, sessionId, text, turn) {
  const sent = await host.call('POST', `/v1/sessions/${sessionId}/messages`, { text })
  assert.equal(sent.status, 202, JSON.stringify(sent.body))
  const ended = frame => isTurnEnd(frame) && frame.data.data.turn === turn
  return (await collectEvents(host, sessionId, ended)).find(ended)
}

const occurrences = (text, mark) => text.split(mark).length - 1

test('输出到上限被截断：这一轮以截断结束；下一轮第一步前告诉模型截在哪一步、写的什么，要拆小；同一次截断只说一次', async () => {
  const env = await startEnv([
    { truncated: { tool: 'write', args: '{"file_path": "/workspace/tmp/deck.js", "content": "const pptxgen = require(' } },
    { text: '上一轮写脚本时输出太长被截断了，我拆开来写' },
    { text: '好的' },
  ])
  try {
    await env.host.call('POST', '/v1/sessions', { sessionId: 'ol-1' })
    const first = await ask(env.host, 'ol-1', '做一份 10 页的 PPT', 1)
    assert.equal(first.data.data.reason.kind, 'max-tokens')

    await ask(env.host, 'ol-1', '为什么回复被截断了？', 2)
    const seen = JSON.stringify(env.chat()[1].body.messages)
    assert.ok(seen.includes(LIMIT_MARK), '下一轮没告诉模型上一轮被截断')
    assert.ok(seen.includes('write') && seen.includes('/workspace/tmp/deck.js'), '没说清截在哪一步、写的什么')
    assert.ok(seen.indexOf(LIMIT_MARK) > seen.indexOf('为什么回复被截断了'), '说明要跟在这一轮的提问后面，不能插进前缀')

    await ask(env.host, 'ol-1', '继续', 3)
    assert.equal(occurrences(JSON.stringify(env.chat()[2].body.messages), LIMIT_MARK), 1, '同一次截断的说明发了不止一次')
  } finally {
    await env.stop()
  }
})

test('正常结束前待办还有没勾的：让模型再走一步把待办更新掉，每轮只提醒一次', async () => {
  const env = await startEnv([
    { tool: 'todo_write', args: { todos: [{ content: '写脚本', status: 'completed' }, { content: '交付文件', status: 'in_progress' }] } },
    { text: '文件已经交付' },
    { tool: 'todo_write', args: { todos: [{ content: '写脚本', status: 'completed' }, { content: '交付文件', status: 'in_progress' }] } },
    { text: '待办已更新' },
  ])
  try {
    await env.host.call('POST', '/v1/sessions', { sessionId: 'td-1' })
    const end = await ask(env.host, 'td-1', '做一份 PPT', 1)
    assert.equal(end.data.data.reason.kind, 'completed', JSON.stringify(end.data.data.reason))
    const requests = env.chat()
    assert.equal(requests.length, 4, '提醒以后应当再走两步（更新待办、收尾），而且只提醒一次')
    const nudged = JSON.stringify(requests[2].body.messages)
    assert.ok(nudged.includes(TODO_MARK) && nudged.includes('交付文件（进行中）'), '提醒里要列出没勾的那一项')
    assert.equal(occurrences(JSON.stringify(requests[3].body.messages), TODO_MARK), 1, '同一轮提醒了不止一次')
  } finally {
    await env.stop()
  }
})

test('待办都勾完了、或者没写过待办：不提醒，照常结束', async () => {
  const env = await startEnv([
    { tool: 'todo_write', args: { todos: [{ content: '写脚本', status: 'completed' }, { content: '交付文件', status: 'completed' }] } },
    { text: '做好了' },
    { text: '没有待办的一轮' },
  ])
  try {
    await env.host.call('POST', '/v1/sessions', { sessionId: 'td-2' })
    await ask(env.host, 'td-2', '做一份 PPT', 1)
    await ask(env.host, 'td-2', '谢谢', 2)
    const requests = env.chat()
    assert.equal(requests.length, 3)
    assert.ok(!requests.some(r => JSON.stringify(r.body.messages).includes(TODO_MARK)))
  } finally {
    await env.stop()
  }
})
