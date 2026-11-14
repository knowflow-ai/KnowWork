/**
 * 向用户提问与待办（DSH 原生能力）：
 * - 官方 ask_user_question（timed 模式）由提问应答者等用户作答，回答由宿主接口代用户提交；提问、回答、迟到回答都只记在
 *   官方日志里（工具调用与结果、user-question-reply 消息），应答者不另发帧；
 * - 前台等待到期或宿主重启后问题仍可答，迟到回答经官方 userQuestions.answer 开新一轮；
 * - 官方 todo_write 落成 todo/write 会话事件。
 * 模型由 openai-stub 扮演，走生产的 pi-ai 适配器（官方 Messages 模拟服务一个进程只能编排一种工具调用）。
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { startHost, collectEvents, isTurnEnd } from './support/harness.mjs'
import { startOpenAiStub } from './support/openai-stub.mjs'

const MODEL_TOKEN = 'gw-token-questions-'.padEnd(40, 'q')
const QUESTIONS = [{ id: 'fmt', question: '导出成什么格式？', header: '格式', options: [{ label: 'Excel (Recommended)' }, { label: 'PDF' }] }]
const ASK = { tool: 'ask_user_question', args: { questions: QUESTIONS } }

const sessionEvents = frames => frames.filter(f => f.kind === 'session/event').map(f => f.data)
const turnEndReason = frames => sessionEvents(frames).filter(e => e.type === 'turn/end').at(-1)?.data?.reason?.kind
const lastCursor = frames => frames.at(-1)?.cursor ?? 0
const messagesText = request => JSON.stringify(request?.body?.messages ?? [])
/** 官方日志里模型调 ask_user_question 的那条 tool/call */
const isAsk = f => f.kind === 'session/event' && f.data?.type === 'tool/call' && f.data.data?.name === 'ask_user_question'
const askOf = frames => frames.find(isAsk)?.data.data
/** 这次提问的工具结果：{ isError, body }，body 是结果文字按 JSON 读出来的样子 */
function resultOf(frames, callId) {
  const message = sessionEvents(frames).find(e => e.type === 'tool/result' && e.data.message?.toolCallId === callId)?.data.message
  if (message === undefined) return undefined
  const text = message.content?.find(block => block.type === 'text')?.text ?? ''
  let body
  try { body = JSON.parse(text) } catch { body = text }
  return { isError: message.isError === true, body }
}
/** 迟到回答：官方 user-question-reply 用户消息里的回答批次 */
function lateReplyOf(frames, callId) {
  const message = sessionEvents(frames).find(e => e.type === 'user/message' && e.data.source?.kind === 'user-question-reply'
    && e.data.source.callId === callId)?.data
  return message === undefined ? undefined : JSON.parse(message.content.find(block => block.type === 'text').text).answers
}
const noQuestionFrames = frames => !frames.some(f => f.kind.startsWith('question/'))

/** 等应答者登记待答提问：日志里的 tool/call 比它早一点 */
async function pendingQuestions(host, sessionId, count = 1, timeoutMs = 10_000) {
  const deadline = Date.now() + timeoutMs
  for (;;) {
    const listed = await host.call('GET', `/v1/sessions/${sessionId}/questions`)
    if (listed.status !== 200 || listed.body.length >= count || Date.now() > deadline) return listed.body
    await new Promise(resolve => setTimeout(resolve, 50))
  }
}

/** script 是数组时按请求顺序取；是函数时按请求内容选（几个会话共用一个模型桩时用）。 */
async function startQuestionHost(script, env = {}, options = {}) {
  const gateway = await startOpenAiStub(typeof script === 'function'
    ? { pick: script, token: () => MODEL_TOKEN }
    : { script, token: () => MODEL_TOKEN })
  const host = await startHost({ sequence: ['success'] }, {
    KNOWWORK_MODEL_PROVIDER: 'knowwork-gateway',
    KNOWWORK_MODEL: 'kw-chat',
    KNOWWORK_MODEL_BASE_URL: gateway.baseURL,
    KNOWWORK_MODEL_TOKEN: MODEL_TOKEN,
    ...env,
  }, options)
  return {
    gateway,
    host,
    stop: async (opts) => { await host.stop(opts); await gateway.close() },
  }
}

test('前台等待期内作答：回答先核对再交回，工具结果就是回答，回合接着跑', async () => {
  const env = await startQuestionHost([ASK, { text: '好的，按 PDF 导出' }])
  try {
    const { host } = env
    await host.call('POST', '/v1/sessions', { sessionId: 'q-open' })
    await host.call('POST', '/v1/sessions/q-open/messages', { text: '导出报表' })
    const asked = await collectEvents(host, 'q-open', isAsk)
    const call = askOf(asked)
    assert.equal(call.callId, 'call_1', '提问以工具调用 id 为身份')
    assert.deepEqual(JSON.parse(call.arguments).questions, QUESTIONS)

    const listed = await pendingQuestions(host, 'q-open')
    assert.deepEqual(listed.map(q => [q.id, q.state]), [['call_1', 'open']])

    for (const answers of [[{ id: 'fmt', selected: ['Word'] }], [{ id: 'fmt', selected: ['PDF', 'Excel (Recommended)'] }], [{ id: 'x', selected: [] }]]) {
      const rejected = await host.call('POST', '/v1/sessions/q-open/questions/call_1', { answers })
      assert.equal(rejected.status, 400, JSON.stringify(answers))
      assert.equal(rejected.body.error.code, 'invalid_answer')
    }
    const accepted = await host.call('POST', '/v1/sessions/q-open/questions/call_1', { answers: [{ id: 'fmt', selected: ['PDF'] }] })
    assert.deepEqual(accepted.body, { questionId: 'call_1', state: 'answered' })

    const rest = await collectEvents(host, 'q-open', isTurnEnd, { after: lastCursor(asked) })
    assert.deepEqual(resultOf(rest, 'call_1'), { isError: false, body: { answers: [{ id: 'fmt', selected: ['PDF'] }] } })
    assert.ok(noQuestionFrames([...asked, ...rest]), '不另发提问帧，只认官方日志')
    assert.equal(turnEndReason(rest), 'completed')
    assert.ok(messagesText(env.gateway.requests[1]).includes('\\"selected\\":[\\"PDF\\"]'), '模型没有收到回答')
    assert.deepEqual((await host.call('GET', '/v1/sessions/q-open/questions')).body, [])
    assert.equal((await host.call('POST', '/v1/sessions/q-open/questions/call_1', { answers: [{ id: 'fmt', selected: ['PDF'] }] })).status, 404)
  } finally {
    await env.stop()
  }
})

test('前台等待到期：问题转入 continued，迟到回答以官方 user-question-reply 开新一轮', async () => {
  const env = await startQuestionHost([
    { tool: 'ask_user_question', args: { questions: QUESTIONS, timeout: 1 } },
    { text: '先按默认格式准备，等你确认' },
    { text: '收到，改成 PDF' },
  ])
  try {
    const { host } = env
    await host.call('POST', '/v1/sessions', { sessionId: 'q-late' })
    await host.call('POST', '/v1/sessions/q-late/messages', { text: '导出报表' })
    const first = await collectEvents(host, 'q-late', isTurnEnd)
    assert.equal(resultOf(first, 'call_1')?.body.pending, true, '前台等待到期后工具结果是 pending，问题仍可答')
    assert.equal(turnEndReason(first), 'completed')

    const listed = await host.call('GET', '/v1/sessions/q-late/questions')
    assert.deepEqual(listed.body.map(q => [q.id, q.state]), [['call_1', 'continued']])
    assert.equal((await host.call('POST', '/v1/sessions/q-late/questions/call_1', { answers: [{ id: 'fmt', selected: ['Word'] }] })).status, 400)
    const queued = await host.call('POST', '/v1/sessions/q-late/questions/call_1', { answers: [{ id: 'fmt', selected: ['PDF'] }] })
    assert.deepEqual(queued.body, { questionId: 'call_1', state: 'queued' })

    const second = await collectEvents(host, 'q-late', isTurnEnd, { after: lastCursor(first) })
    assert.ok(sessionEvents(second).some(e => e.type === 'turn/start'), '迟到回答应开新一轮')
    assert.deepEqual(lateReplyOf(second, 'call_1'), [{ id: 'fmt', selected: ['PDF'] }], '迟到回答记成 user-question-reply 消息')
    assert.ok(noQuestionFrames([...first, ...second]))
    assert.equal(turnEndReason(second), 'completed')
    assert.ok(messagesText(env.gateway.requests.at(-1)).includes('answer_to_pending_question'), '模型没有收到迟到回答')
    assert.deepEqual((await host.call('GET', '/v1/sessions/q-late/questions')).body, [])
  } finally {
    await env.stop()
  }
})

test('不限时的提问到等待上限按超时关闭；取消回合与释放会话时提问结算为取消', async () => {
  // 每个会话第一次请求提问，拿到工具结果后收尾
  const pick = body => body.messages.some(m => m.role === 'tool')
    ? { text: '没等到回答，先停在这里' }
    : { tool: 'ask_user_question', args: { questions: QUESTIONS, timeout: -1 } }
  const env = await startQuestionHost(pick, { KNOWWORK_QUESTION_TIMEOUT_MS: '1500' })
  try {
    const { host } = env
    await host.call('POST', '/v1/sessions', { sessionId: 'q-cap' })
    await host.call('POST', '/v1/sessions/q-cap/messages', { text: '导出报表' })
    const capped = await collectEvents(host, 'q-cap', isTurnEnd)
    const result = sessionEvents(capped).find(e => e.type === 'tool/result')
    assert.equal(result.data.message.isError, true, '超时的不限时提问应以工具错误结束')
    assert.deepEqual((await host.call('GET', '/v1/sessions/q-cap/questions')).body, [], '超时关闭的问题不能再答')

    await host.call('POST', '/v1/sessions', { sessionId: 'q-cancel' })
    await host.call('POST', '/v1/sessions/q-cancel/messages', { text: '导出报表' })
    const asked = await collectEvents(host, 'q-cancel', isAsk)
    await pendingQuestions(host, 'q-cancel')
    assert.equal((await host.call('POST', '/v1/sessions/q-cancel/cancel')).status, 200)
    const cancelled = await collectEvents(host, 'q-cancel', isTurnEnd, { after: lastCursor(asked) })
    assert.equal(resultOf(cancelled, askOf(asked).callId)?.isError, true, '取消回合时提问以工具错误结束')

    await host.call('POST', '/v1/sessions', { sessionId: 'q-drop' })
    await host.call('POST', '/v1/sessions/q-drop/messages', { text: '导出报表' })
    await collectEvents(host, 'q-drop', isAsk)
    await pendingQuestions(host, 'q-drop')
    const started = Date.now()
    assert.equal((await host.call('DELETE', '/v1/sessions/q-drop')).status, 200)
    assert.ok(Date.now() - started < 5000, '释放被待答提问卡住')
  } finally {
    await env.stop()
  }
})

async function askThenStop(signal) {
  const first = await startQuestionHost([ASK, { text: '先等你回答' }])
  await first.host.call('POST', '/v1/sessions', { sessionId: 'q-crash' })
  await first.host.call('POST', '/v1/sessions/q-crash/messages', { text: '导出报表' })
  await collectEvents(first.host, 'q-crash', isAsk)
  await pendingQuestions(first.host, 'q-crash')
  await first.stop({ keepHome: true, signal })
  return first.host.home
}

test('宿主在等待中被杀：恢复会话后没答完的问题仍可回答', async () => {
  const home = await askThenStop('SIGKILL')
  const second = await startQuestionHost([{ text: '收到，按 PDF 导出' }], {}, { home })
  try {
    const { host } = second
    assert.equal((await host.call('POST', '/v1/sessions', { sessionId: 'q-crash', resume: true, policy: {} })).status, 201)
    const listed = await host.call('GET', '/v1/sessions/q-crash/questions')
    assert.deepEqual(listed.body.map(q => [q.id, q.state]), [['call_1', 'continued']])
    assert.deepEqual(listed.body[0].questions, QUESTIONS)
    const queued = await host.call('POST', '/v1/sessions/q-crash/questions/call_1', { answers: [{ id: 'fmt', selected: ['PDF'] }] })
    assert.equal(queued.status, 200)
    const frames = await collectEvents(host, 'q-crash', isTurnEnd)
    assert.deepEqual(lateReplyOf(frames, 'call_1'), [{ id: 'fmt', selected: ['PDF'] }])
    assert.equal(turnEndReason(frames), 'completed')
    assert.ok(messagesText(second.gateway.requests.at(-1)).includes('answer_to_pending_question'))
  } finally {
    await second.stop()
  }
})

test('上游行为哨兵：宿主正常关闭时官方把前台等待中的提问记成已中止，恢复后不能再答', async () => {
  // 失败了说明上游改了关闭语义，问题能跨正常重启保留了：同步更新 question-broker 注释与提问卡片文案
  const home = await askThenStop('SIGTERM')
  const second = await startQuestionHost([{ text: '收到' }], {}, { home })
  try {
    const { host } = second
    assert.equal((await host.call('POST', '/v1/sessions', { sessionId: 'q-crash', resume: true, policy: {} })).status, 201)
    assert.deepEqual((await host.call('GET', '/v1/sessions/q-crash/questions')).body, [])
    const answered = await host.call('POST', '/v1/sessions/q-crash/questions/call_1', { answers: [{ id: 'fmt', selected: ['PDF'] }] })
    assert.equal(answered.body.error?.code, 'question_not_found')
  } finally {
    await second.stop()
  }
})

test('待办：todo_write 整表写入，落成 todo/write 会话事件；非法列表以工具错误返回', async () => {
  const todos = [{ content: '读取合同', status: 'completed' }, { content: '抽取条款', status: 'in_progress' }, { content: '生成台账', status: 'pending' }]
  const env = await startQuestionHost([
    { tool: 'todo_write', args: { todos } },
    { tool: 'todo_write', args: { todos: [{ content: '重复', status: 'pending' }, { content: '重复', status: 'pending' }] } },
    { text: '计划已列好' },
  ])
  try {
    const { host } = env
    await host.call('POST', '/v1/sessions', { sessionId: 'todo' })
    await host.call('POST', '/v1/sessions/todo/messages', { text: '整理合同台账' })
    const frames = await collectEvents(host, 'todo', isTurnEnd)
    const writes = sessionEvents(frames).filter(e => e.type === 'todo/write')
    assert.deepEqual(writes.map(e => e.data.todos), [todos], '非法列表不应写入')
    const results = sessionEvents(frames).filter(e => e.type === 'tool/result')
    assert.deepEqual(results.map(e => e.data.message.isError === true), [false, true])
    assert.ok(env.gateway.requests[0].body.tools.some(t => t.function?.name === 'todo_write'), '模型看不到 todo_write')
    assert.equal(turnEndReason(frames), 'completed')
  } finally {
    await env.stop()
  }
})
