/**
 * 会话策略对业务工具生效（实验 E2 附带发现）：deny 业务工具名不再让会话打不开，
 * 被拒的业务工具对模型不可见、凭空调用也不回调业务服务；未知名字忽略并记宿主日志。
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { startHost, collectEvents, isTurnEnd } from './support/harness.mjs'
import { bizTool, startToolsStub } from './support/tools-stub.mjs'

const TOKEN = 'policy-token-'.padEnd(40, 'p')
const toolNamesOf = request => (request.body?.tools ?? []).map(t => t.name)

async function startPolicyHost(mock) {
  const stub = await startToolsStub({ toolsByToken: { [TOKEN]: [bizTool('kw_biz_open'), bizTool('kw_biz_denied', 'write')] } })
  const host = await startHost(mock, { KNOWWORK_TOOLS_BASE_URL: stub.baseUrl })
  return { host, stub, stop: async () => { await host.stop(); await stub.close() } }
}

test('deny 业务工具：会话照常打开，模型看不到它，其余工具不受影响', async () => {
  const env = await startPolicyHost({ sequence: ['success'] })
  try {
    const opened = await env.host.call('POST', '/v1/sessions', { sessionId: 'sp-biz', policy: { deny: ['kw_biz_denied', 'kw_echo'] }, tools: { token: TOKEN } })
    assert.equal(opened.status, 201, JSON.stringify(opened.body))
    await env.host.call('POST', '/v1/sessions/sp-biz/messages', { text: '看看有哪些工具' })
    await collectEvents(env.host, 'sp-biz', isTurnEnd)
    const names = toolNamesOf(env.host.llm.requests[0])
    assert.ok(names.includes('kw_biz_open'), '未被拒的业务工具丢了')
    assert.ok(!names.includes('kw_biz_denied'), '被拒的业务工具仍对模型可见')
    assert.ok(!names.includes('kw_echo'), '被拒的全局工具仍对模型可见')
    assert.ok(names.includes('kw_write'), '未被拒的全局工具丢了')
  } finally {
    await env.stop()
  }
})

test('deny 业务工具：模型凭空调用也不会回调业务服务', async () => {
  const env = await startPolicyHost({ sequence: ['tool_call_success', 'success'], toolName: 'kw_biz_denied', toolArguments: JSON.stringify({ q: '越权' }) })
  try {
    await env.host.call('POST', '/v1/sessions', { sessionId: 'sp-forge', policy: { deny: ['kw_biz_denied'] }, tools: { token: TOKEN } })
    await env.host.call('POST', '/v1/sessions/sp-forge/messages', { text: '调用被拒的工具' })
    await collectEvents(env.host, 'sp-forge', isTurnEnd)
    assert.equal(env.host.llm.requests.length, 2, '模型没有拿到工具结果再请求一次')
    assert.equal(env.stub.executions().length, 0, '被拒的业务工具被执行了')
  } finally {
    await env.stop()
  }
})

test('deny 未知名字：会话照常打开，宿主记一条日志；没开工具桥的会话也一样', async () => {
  const env = await startPolicyHost({ sequence: ['success'] })
  try {
    const withBridge = await env.host.call('POST', '/v1/sessions', { sessionId: 'sp-unknown', policy: { deny: ['kw_no_such_tool'] }, tools: { token: TOKEN } })
    assert.equal(withBridge.status, 201, JSON.stringify(withBridge.body))
    const plain = await env.host.call('POST', '/v1/sessions', { sessionId: 'sp-plain', policy: { deny: ['kw_ghost', 'kw_echo'] } })
    assert.equal(plain.status, 201, JSON.stringify(plain.body))
    await env.host.call('POST', '/v1/sessions/sp-plain/messages', { text: '你好' })
    await collectEvents(env.host, 'sp-plain', isTurnEnd)
    assert.ok(!toolNamesOf(env.host.llm.requests[0]).includes('kw_echo'), '同一名单里的全局工具没有被收窄')
    assert.match(env.host.stderr(), /sp-unknown policy denies unknown tools, ignored: kw_no_such_tool/)
    assert.match(env.host.stderr(), /sp-plain policy denies unknown tools, ignored: kw_ghost/)
  } finally {
    await env.stop()
  }
})

test('恢复会话换上新策略：上一版可用的业务工具在恢复后不可见', async () => {
  const env = await startPolicyHost({ sequence: ['success'] })
  try {
    await env.host.call('POST', '/v1/sessions', { sessionId: 'sp-rot', policy: {}, tools: { token: TOKEN } })
    await env.host.call('POST', '/v1/sessions/sp-rot/messages', { text: '第一轮' })
    await collectEvents(env.host, 'sp-rot', isTurnEnd)
    assert.ok(toolNamesOf(env.host.llm.requests[0]).includes('kw_biz_denied'))
    await env.host.call('DELETE', '/v1/sessions/sp-rot')
    const resumed = await env.host.call('POST', '/v1/sessions', { sessionId: 'sp-rot', resume: true, policy: { deny: ['kw_biz_denied'] }, tools: { token: TOKEN } })
    assert.equal(resumed.status, 201, JSON.stringify(resumed.body))
    const before = env.host.llm.requests.length
    await env.host.call('POST', '/v1/sessions/sp-rot/messages', { text: '第二轮' })
    await collectEvents(env.host, 'sp-rot', frame => isTurnEnd(frame) && env.host.llm.requests.length > before)
    const second = env.host.llm.requests.at(-1)
    assert.ok(JSON.stringify(second.body.messages).includes('第一轮'), '恢复后丢了历史')
    assert.ok(!toolNamesOf(second).includes('kw_biz_denied'), '恢复后仍看得到新策略拒绝的工具')
  } finally {
    await env.stop()
  }
})
