/**
 * 多用户适配层 + 官方定时任务（multi-user/）：官方 ScheduleService 与 schedule_* 工具原样，适配层只补归属与改道。
 * - 谁建的任务归谁：同一个人在另一段对话里看得到，别人列不出、按 id 也查不到（照官方「找不到」）；
 * - 投递目标第一版只支持这个对话：选新开对话被拦下，原因给模型看；时间写成 UTC 也被拦下，要按时区写本地时间；
 * - 到点时先请业务服务激活（带宿主令牌，说清哪类插件、哪段对话、归谁），业务服务点头才投递；不点头就不投；
 *   宿主上已经没有这段对话时（空闲释放、宿主重启）属主从账本里取，业务服务重新打开对话后照常投递。
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { startHost, collectEvents, isTurnEnd } from './support/harness.mjs'
import { startOpenAiStub } from './support/openai-stub.mjs'
import { startToolsStub } from './support/tools-stub.mjs'

const TOKENS = { a: 'mu-token-a-'.padEnd(40, 'a'), b: 'mu-token-b-'.padEnd(40, 'b'), c: 'mu-token-c-'.padEnd(40, 'c') }
const OWNERS = { a: { workspaceId: 'ws1', userId: 'alice' }, b: { workspaceId: 'ws1', userId: 'bob' } }
// 时间按时区写本地时间（适配层只收这种写法，见 schedule.mjs）
const LATER = 'DTSTART;TZID=Asia/Shanghai:20990101T090000\nRRULE:FREQ=DAILY;COUNT=1'

/** 一次性：几秒后到点（精确到秒；用 Etc/UTC，本地时间就是 UTC 的钟点） */
function soon(seconds) {
  const at = new Date(Date.now() + seconds * 1000).toISOString().replace(/[-:]/g, '').replace(/\.\d+Z$/, '')
  return `DTSTART;TZID=Etc/UTC:${at}\nRRULE:FREQ=DAILY;COUNT=1`
}

/** 业务服务「激活」接口的测试桩：记下请求，activate 决定点不点头（也可以像业务服务那样先把对话打开）。 */
async function startActivationStub(activate) {
  const requests = []
  const server = createServer(async (req, res) => {
    let raw = ''
    for await (const chunk of req) raw += chunk
    const body = JSON.parse(raw || '{}')
    requests.push({ path: req.url, authorization: req.headers.authorization, body })
    const ok = await activate(body)
    res.writeHead(ok ? 200 : 403, { 'content-type': 'application/json' })
    res.end(JSON.stringify(ok ? { ok: true } : { detail: '这个人不是这个空间的成员' }))
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  return { baseUrl: `http://127.0.0.1:${server.address().port}/internal/host/v1`, requests,
    close: () => new Promise(resolve => server.close(resolve)) }
}

/**
 * 按提问编排模型：找最后一条带某个标记的用户消息（这一轮是谁发起的：用户的提问或到点的提醒），步骤按它之后已有的
 * 工具结果数往下走。只认用户消息里的标记；官方定时任务工具的说明（也是一条用户消息）里写着 [SCHEDULE REMINDER]
 * 这些字样，不能拿它当标记。
 */
function byConversation(plans) {
  return body => {
    const messages = body.messages ?? []
    let at = -1
    let plan
    messages.forEach((m, index) => {
      if (m.role !== 'user') return
      const found = plans.find(p => JSON.stringify(m.content).includes(p.marker))
      if (found !== undefined) { at = index; plan = found }
    })
    const step = plan?.steps[messages.slice(at + 1).filter(m => m.role === 'tool').length] ?? { text: '好' }
    return typeof step === 'function' ? step(messages) : step
  }
}

/** 最后一次模型请求里这段对话的全部工具结果（每次请求都带着之前的） */
const toolResults = env => env.gateway.requests.filter(r => r.path.endsWith('/chat/completions')).at(-1)
  .body.messages.filter(m => m.role === 'tool').map(m => String(m.content))

async function startScheduleHost(plans, { activate = async () => true } = {}) {
  const gateway = await startOpenAiStub({ pick: byConversation(plans) })
  const tools = await startToolsStub({ toolsByToken: { [TOKENS.a]: [], [TOKENS.b]: [], [TOKENS.c]: [] } })
  // 激活时要能回头调宿主（像业务服务那样重新打开对话），宿主起来后才有 env
  let env
  const activation = await startActivationStub(body => activate(body, env))
  const host = await startHost({ sequence: ['success'] }, {
    KNOWWORK_MODEL_PROVIDER: 'knowwork-gateway',
    KNOWWORK_MODEL: 'kw-chat',
    KNOWWORK_MODEL_BASE_URL: gateway.baseURL,
    KNOWWORK_MODEL_TOKEN: 'gw-token-schedule-0001-aaaa',
    KNOWWORK_TOOLS_BASE_URL: tools.baseUrl,
    KNOWWORK_ACTIVATION_BASE_URL: activation.baseUrl,
  })
  const open = (sessionId, who, token, extra = {}) =>
    host.call('POST', '/v1/sessions', { sessionId, owner: OWNERS[who], tools: { token }, ...extra })
  // 每段对话记着读到哪了：同一段对话再问一次，从上次读到的位置接着等这一轮结束
  const cursors = new Map()
  const ask = async (sessionId, text) => {
    await host.call('POST', `/v1/sessions/${sessionId}/messages`, { text })
    const frames = await collectEvents(host, sessionId, isTurnEnd, { after: cursors.get(sessionId) ?? 0 })
    cursors.set(sessionId, frames.at(-1)?.cursor ?? 0)
  }
  const waitFor = (sessionId, predicate, timeoutMs) =>
    collectEvents(host, sessionId, predicate, { after: cursors.get(sessionId) ?? 0, timeoutMs })
  env = { host, gateway, activation, open, ask, waitFor, forget: sessionId => cursors.delete(sessionId),
    stop: async () => { await host.stop(); await gateway.close(); await tools.close(); await activation.close() } }
  return env
}

const REMIND_IN_3S = { marker: 'A：三秒后提醒', steps: [() => ({ tool: 'schedule_create', args: { title: '提醒', prompt: '该写日报了', rrule: soon(3), target: 'this_session' } })] }
const isReminder = frame => frame.kind === 'session/event' && frame.data?.type === 'user/message' && frame.data.data?.source?.kind === 'schedule'

test('谁建的任务归谁：同一个人换个对话看得到，别人列不出、按 id 查不到；选新开对话、时间写成 UTC 都被拦下，原因给模型', async () => {
  let taskId
  const env = await startScheduleHost([
    { marker: 'A：建任务', steps: [
      { tool: 'schedule_create', args: { title: '日报', prompt: '写日报', rrule: LATER, target: 'this_session' } },
      { tool: 'schedule_create', args: { title: '周报', prompt: '写周报', rrule: LATER, target: 'new' } },
      { tool: 'schedule_create', args: { title: '月报', prompt: '写月报', rrule: 'DTSTART:20990101T010000Z\nRRULE:FREQ=DAILY;COUNT=1', target: 'this_session' } },
    ] },
    { marker: 'B：列一下', steps: [{ tool: 'schedule_list', args: {} }] },
    { marker: 'B：看那条', steps: [() => ({ tool: 'schedule_view', args: { id: taskId } })] },
    { marker: 'C：我的任务', steps: [{ tool: 'schedule_list', args: {} }] },
  ])
  try {
    const bad = await env.host.call('POST', '/v1/sessions', { sessionId: 'mu-x', owner: { workspaceId: 'ws1', userId: '' }, tools: { token: TOKENS.a } })
    assert.equal(bad.status, 400, '属主不合规时不打开会话')
    assert.equal((await env.open('mu-a', 'a', TOKENS.a)).status, 201)
    await env.ask('mu-a', 'A：建任务')
    const [created, denied, utc] = toolResults(env)
    taskId = JSON.parse(created).id
    assert.ok(taskId, `没建成：${created}`)
    assert.equal(JSON.parse(created).scheduledAtLocal, '2099-01-01T09:00:00+08:00', '结果里的本地时间是用户时区的钟点')
    assert.match(denied, /this_session/, '新开对话应被拦下，原因里说用 this_session')
    assert.match(utc, /TZID/, '写成 UTC 应被拦下，原因里说按时区写本地时间')

    await env.open('mu-b', 'b', TOKENS.b)
    await env.ask('mu-b', 'B：列一下')
    assert.equal(JSON.parse(toolResults(env).at(-1)).total, 0, '别人的任务不该列出来')
    await env.ask('mu-b', 'B：看那条')
    assert.equal(JSON.parse(toolResults(env).at(-1)).code, 'schedule_not_found', '按 id 查别人的任务应当查不到')

    await env.open('mu-c', 'a', TOKENS.c)
    await env.ask('mu-c', 'C：我的任务')
    const mine = JSON.parse(toolResults(env).at(-1))
    assert.equal(mine.total, 1, '同一个人在另一段对话里应看得到')
    assert.equal(mine.items[0].id, taskId)
  } finally {
    await env.stop()
  }
})

test('到点先请业务服务激活（宿主令牌，说清插件、对话与属主），点头后投进这段对话；不点头就不投', async () => {
  const env = await startScheduleHost([
    // 到点投进来的提醒里带着任务的提示词（reminder_prompt_json）；官方工具的说明里没有它
    { marker: '该写日报了', steps: [{ text: '日报写好了' }] },
    REMIND_IN_3S,
  ])
  try {
    await env.open('mu-a', 'a', TOKENS.a)
    await env.ask('mu-a', 'A：三秒后提醒')
    const delivered = (await env.waitFor('mu-a', isReminder, 20_000)).at(-1).data.data
    assert.match(delivered.content[0].text, /该写日报了/)
    const [request] = env.activation.requests
    assert.equal(request.path, '/internal/host/v1/activations')
    assert.equal(request.authorization, `Bearer ${env.host.token}`)
    assert.deepEqual(request.body, { kind: 'schedule', sessionId: 'mu-a', owner: OWNERS.a })
  } finally {
    await env.stop()
  }

  const refused = await startScheduleHost([REMIND_IN_3S], { activate: async () => false })
  try {
    await refused.open('mu-a', 'a', TOKENS.a)
    await refused.ask('mu-a', 'A：三秒后提醒')
    await new Promise(resolve => setTimeout(resolve, 6000))
    assert.ok(refused.activation.requests.length > 0, '到点应当先问业务服务')
    const reminders = refused.gateway.requests.filter(r => JSON.stringify(r.body?.messages ?? []).includes('该写日报了') &&
      JSON.stringify(r.body?.messages ?? []).includes('[SCHEDULE REMINDER BATCH]'))
    assert.equal(reminders.length, 0, '业务服务没点头，不该投递')
  } finally {
    await refused.stop()
  }
})

test('到点时宿主上已经没有这段对话：属主从账本里取，业务服务重新打开后照常投递', async () => {
  let reopened
  const ready = new Promise(resolve => { reopened = resolve })
  const env = await startScheduleHost([
    { marker: '该写日报了', steps: [{ text: '日报写好了' }] },
    REMIND_IN_3S,
  ], {
    // 业务服务的做法：按属主核对后装好令牌与策略，恢复宿主上的这段对话
    activate: async (body, host) => {
      const response = await host.open(body.sessionId, 'a', TOKENS.a, { resume: true, policy: {} })
      reopened()
      return response.status === 201
    },
  })
  try {
    await env.open('mu-a', 'a', TOKENS.a)
    await env.ask('mu-a', 'A：三秒后提醒')
    assert.equal((await env.host.call('DELETE', '/v1/sessions/mu-a')).status, 200)
    env.forget('mu-a')
    await ready
    const delivered = (await env.waitFor('mu-a', isReminder, 20_000)).at(-1).data.data
    assert.match(delivered.content[0].text, /该写日报了/)
    assert.deepEqual(env.activation.requests[0].body, { kind: 'schedule', sessionId: 'mu-a', owner: OWNERS.a })
  } finally {
    await env.stop()
  }
})
