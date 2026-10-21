/**
 * 官方子 Agent 在宿主会话里打开，并按父会话继承策略：工具收窄、业务工具、审批、事件。
 *
 * 父子两个 Agent 的模型请求会并发到达，按到达顺序编排不确定，所以用 openai-stub 的 pick
 * 按请求内容区分角色（官方给子 Agent 的首条消息里带「Your parent agent id is」），各自按自己的请求次数出回答。
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { startHost, collectEvents } from './support/harness.mjs'
import { startOpenAiStub } from './support/openai-stub.mjs'
import { bizTool, startToolsStub } from './support/tools-stub.mjs'

const TOKEN = 'tools-token-sub-'.padEnd(40, 's')
const DELEGATION = 'Your parent agent id is'
const isChildRequest = body => JSON.stringify(body).includes(DELEGATION)
const toolNamesOf = body => (body?.tools ?? []).map(t => t.function?.name)
const subagentEvent = type => f => f.kind === 'subagent/event' && f.data.event?.type === type

/** 父 Agent 第一步委派子 Agent，之后只回答文字；子 Agent 第一步调用 childTool，之后回答文字。 */
function delegationScript(childTool, childArgs) {
  const counts = { parent: 0, child: 0 }
  return body => {
    const role = isChildRequest(body) ? 'child' : 'parent'
    const index = counts[role]++
    if (role === 'parent') {
      return index === 0 ? { tool: 'subagent', args: { description: '子任务', prompt: '请处理子任务' } } : { text: '父会话已收到子任务结果' }
    }
    return index === 0 ? { tool: childTool, args: childArgs } : { text: '子任务完成' }
  }
}

async function startDelegationHost(childTool, childArgs) {
  const gateway = await startOpenAiStub({ pick: delegationScript(childTool, childArgs) })
  const tools = await startToolsStub({ toolsByToken: { [TOKEN]: [bizTool('kw_biz_a')] } })
  const host = await startHost({ sequence: ['success'] }, {
    KNOWWORK_MODEL_PROVIDER: 'knowwork-gateway',
    KNOWWORK_MODEL: 'kw-chat',
    KNOWWORK_MODEL_BASE_URL: gateway.baseURL,
    KNOWWORK_MODEL_TOKEN: 'gw-token-sub-0001-aaaaaaaa',
    KNOWWORK_TOOLS_BASE_URL: tools.baseUrl,
  })
  const chat = () => gateway.requests.filter(r => r.path.endsWith('/chat/completions'))
  return {
    host,
    tools,
    parentRequests: () => chat().filter(r => !isChildRequest(r.body)),
    childRequests: () => chat().filter(r => isChildRequest(r.body)),
    stop: async () => { await host.stop(); await gateway.close(); await tools.close() },
  }
}

test('子 Agent 继承父会话：收窄的工具看不到，业务工具可用且用根会话令牌，事件转发到父会话流', async () => {
  const env = await startDelegationHost('kw_biz_a', { q: '子任务查询' })
  try {
    await env.host.call('POST', '/v1/sessions', { sessionId: 'sub-1', policy: { deny: ['kw_echo'] }, tools: { token: TOKEN } })
    await env.host.call('POST', '/v1/sessions/sub-1/messages', { text: '把工作交给子 Agent' })
    const frames = await collectEvents(env.host, 'sub-1', subagentEvent('turn/end'), { timeoutMs: 30_000 })

    const parentTools = toolNamesOf(env.parentRequests()[0].body)
    assert.ok(parentTools.includes('subagent'), `父会话没有官方委派工具：${parentTools}`)
    const [childFirst, childSecond] = env.childRequests()
    assert.ok(childFirst, '子 Agent 没有发出模型请求')
    const childTools = toolNamesOf(childFirst.body)
    assert.ok(!childTools.includes('kw_echo'), `父会话收窄的工具子 Agent 仍看得到：${childTools}`)
    assert.ok(childTools.includes('kw_finalize'), `子 Agent 看不到未收窄的工具：${childTools}`)
    assert.ok(childTools.includes('kw_biz_a'), `子 Agent 看不到业务工具：${childTools}`)

    const childId = frames.find(subagentEvent('turn/end')).data.sessionId
    assert.notEqual(childId, 'sub-1')
    const [call] = env.tools.executions()
    assert.ok(call, '子 Agent 的业务工具调用没有到业务服务')
    assert.equal(call.token, TOKEN, '子 Agent 没有使用根会话的令牌')
    assert.equal(call.body.agentSessionId, childId)
    assert.equal(call.idempotencyKey, `${childId}:${call.body.callId}`)
    assert.ok(JSON.stringify(childSecond.body.messages).includes('kw_biz_a 结果'), '业务工具结果没有回到子 Agent 的下一次请求')

    const childEvents = frames.filter(f => f.kind === 'subagent/event' && f.data.sessionId === childId)
    assert.ok(childEvents.some(f => f.data.event.type === 'turn/start'), '父会话流里看不到子会话的回合开始')
    assert.ok(JSON.stringify(childEvents).includes('子任务完成'), '父会话流里看不到子 Agent 的回答')
    assert.ok(frames.some(f => f.kind === 'subagent/status' && f.data.sessionId === childId), '父会话流里看不到子会话状态')
  } finally {
    await env.stop()
  }
})

test('子 Agent 里需要审批的工具：审批出现在父会话的审批列表里，由外部决定后执行', async () => {
  const env = await startDelegationHost('kw_write', { text: '子台账' })
  try {
    await env.host.call('POST', '/v1/sessions', { sessionId: 'sub-appr' })
    await env.host.call('POST', '/v1/sessions/sub-appr/messages', { text: '让子 Agent 写台账' })
    const asked = await collectEvents(env.host, 'sub-appr', f => f.kind === 'approval/requested', { timeoutMs: 30_000 })
    const requested = asked.find(f => f.kind === 'approval/requested').data
    assert.notEqual(requested.sessionId, 'sub-appr', '审批应当来自子会话')
    const pending = (await env.host.call('GET', '/v1/sessions/sub-appr/approvals')).body
    assert.equal(pending.length, 1)
    assert.equal(pending[0].toolName, 'kw_write')
    assert.equal(pending[0].sessionId, requested.sessionId)
    assert.equal((await env.host.call('POST', `/v1/sessions/sub-appr/approvals/${pending[0].id}`, { outcome: 'allowed-once' })).status, 200)
    await collectEvents(env.host, 'sub-appr', subagentEvent('turn/end'), { after: asked.at(-1).cursor, timeoutMs: 30_000 })
    const followup = env.childRequests()[1]
    assert.ok(followup, '审批后子 Agent 没有继续')
    assert.ok(JSON.stringify(followup.body.messages).includes('written: 子台账'), '批准后子 Agent 的工具没有执行')
  } finally {
    await env.stop()
  }
})

test('父会话释放时，子会话的待决审批随之取消，写入不执行', async () => {
  const env = await startDelegationHost('kw_write', { text: '不该写入' })
  try {
    await env.host.call('POST', '/v1/sessions', { sessionId: 'sub-rej' })
    await env.host.call('POST', '/v1/sessions/sub-rej/messages', { text: '让子 Agent 写台账' })
    await collectEvents(env.host, 'sub-rej', f => f.kind === 'approval/requested', { timeoutMs: 30_000 })
    const started = Date.now()
    assert.equal((await env.host.call('DELETE', '/v1/sessions/sub-rej')).status, 200)
    assert.ok(Date.now() - started < 10_000, '释放被子会话的待决审批卡住')
    assert.ok(!env.childRequests().some(r => JSON.stringify(r.body.messages).includes('written: 不该写入')), '未批准的写入被执行了')
  } finally {
    await env.stop()
  }
})
