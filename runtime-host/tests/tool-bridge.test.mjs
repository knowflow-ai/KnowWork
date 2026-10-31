/**
 * 工具桥：业务工具按会话注册、执行回调业务服务、写入工具经官方审批、失败关闭、令牌续期。
 * 真实 DSH 组合 + 官方模拟模型；业务服务由 tools-stub 扮演。断言落到模型请求与业务服务收到的调用上。
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { startHost, collectEvents, isTurnEnd } from './support/harness.mjs'
import { bizTool, startToolsStub } from './support/tools-stub.mjs'
import { startOpenAiStub } from './support/openai-stub.mjs'

const TOKEN_A = 'tools-token-a-'.padEnd(40, 'a')
const TOKEN_B = 'tools-token-b-'.padEnd(40, 'b')
const toolNamesOf = request => (request.body?.tools ?? []).map(t => t.name)
const messagesText = request => JSON.stringify(request.body?.messages ?? [])
const sessionEvents = frames => frames.filter(f => f.kind === 'session/event').map(f => f.data)
const turnEndReason = frames => sessionEvents(frames).find(e => e.type === 'turn/end')?.data?.reason?.kind
const lastCursor = frames => frames.at(-1)?.cursor ?? 0
const systemOf = request => String(request.body?.system ?? '')

async function startBridgeHost(mock, toolsByToken, execute, notes) {
  const stub = await startToolsStub({ toolsByToken, execute, notes })
  const host = await startHost(mock, { KNOWWORK_TOOLS_BASE_URL: stub.baseUrl })
  return { host, stub, stop: async () => { await host.stop(); await stub.close() } }
}

test('业务工具按会话注册：每个会话只看到自己令牌对应的工具', async () => {
  const env = await startBridgeHost({ sequence: ['success'] }, { [TOKEN_A]: [bizTool('kw_biz_a')], [TOKEN_B]: [bizTool('kw_biz_b')] })
  try {
    assert.equal((await env.host.call('POST', '/v1/sessions', { sessionId: 'tb-a', tools: { token: TOKEN_A } })).status, 201)
    assert.equal((await env.host.call('POST', '/v1/sessions', { sessionId: 'tb-b', tools: { token: TOKEN_B } })).status, 201)
    await env.host.call('POST', '/v1/sessions', { sessionId: 'tb-none' })
    for (const id of ['tb-a', 'tb-b', 'tb-none']) {
      await env.host.call('POST', `/v1/sessions/${id}/messages`, { text: `会话 ${id}` })
      await collectEvents(env.host, id, isTurnEnd)
    }
    const requestOf = id => env.host.llm.requests.find(r => messagesText(r).includes(`会话 ${id}`))
    assert.ok(toolNamesOf(requestOf('tb-a')).includes('kw_biz_a'))
    assert.ok(!toolNamesOf(requestOf('tb-a')).includes('kw_biz_b'), '会话 A 看到了会话 B 的工具')
    assert.ok(toolNamesOf(requestOf('tb-b')).includes('kw_biz_b'))
    assert.ok(!toolNamesOf(requestOf('tb-b')).includes('kw_biz_a'), '会话 B 看到了会话 A 的工具')
    assert.ok(!toolNamesOf(requestOf('tb-none')).some(n => n.startsWith('kw_biz')), '没带令牌的会话看到了业务工具')
    const lists = env.stub.requests.filter(r => r.path.endsWith('/list'))
    assert.deepEqual(lists.map(r => r.token).sort(), [TOKEN_A, TOKEN_B])
  } finally {
    await env.stop()
  }
})

test('执行回调业务服务：带令牌与幂等键，结果回到下一次模型请求', async () => {
  const env = await startBridgeHost(
    { sequence: ['tool_call_success', 'success'], toolName: 'kw_biz_a', toolArguments: JSON.stringify({ q: '采购合同' }) },
    { [TOKEN_A]: [bizTool('kw_biz_a')] },
  )
  try {
    await env.host.call('POST', '/v1/sessions', { sessionId: 'tb-exec', tools: { token: TOKEN_A } })
    await env.host.call('POST', '/v1/sessions/tb-exec/messages', { text: '查合同' })
    const frames = await collectEvents(env.host, 'tb-exec', isTurnEnd)
    assert.equal(turnEndReason(frames), 'completed')
    const [call] = env.stub.executions()
    assert.ok(call, '业务服务没有收到执行回调')
    assert.equal(call.token, TOKEN_A)
    assert.deepEqual(call.body, { toolName: 'kw_biz_a', arguments: { q: '采购合同' }, agentSessionId: 'tb-exec', callId: 'mock-call-1' })
    assert.equal(call.idempotencyKey, 'tb-exec:mock-call-1')
    assert.equal(env.host.llm.requests.length, 2)
    assert.ok(messagesText(env.host.llm.requests[1]).includes('kw_biz_a 结果：{\\"q\\":\\"采购合同\\"}'), '工具结果没有回到下一次模型请求')
  } finally {
    await env.stop()
  }
})

test('写入工具执行前经官方审批：批准才回调业务服务，拒绝则不回调', async () => {
  for (const outcome of ['allowed-once', 'rejected']) {
    const env = await startBridgeHost(
      { sequence: ['tool_call_success', 'success'], toolName: 'kw_biz_write', toolArguments: JSON.stringify({ q: '台账' }) },
      { [TOKEN_A]: [bizTool('kw_biz_write', 'write')] },
    )
    try {
      await env.host.call('POST', '/v1/sessions', { sessionId: 'tb-appr', tools: { token: TOKEN_A } })
      await env.host.call('POST', '/v1/sessions/tb-appr/messages', { text: '写台账' })
      const asked = await collectEvents(env.host, 'tb-appr', f => f.kind === 'approval/requested')
      assert.equal(env.stub.executions().length, 0, '审批之前就执行了')
      const pending = (await env.host.call('GET', '/v1/sessions/tb-appr/approvals')).body
      assert.equal(pending.length, 1)
      assert.equal(pending[0].toolName, 'kw_biz_write')
      assert.equal(pending[0].sessionId, 'tb-appr')
      assert.match(pending[0].reason, /写入操作/)
      await env.host.call('POST', `/v1/sessions/tb-appr/approvals/${pending[0].id}`, { outcome })
      const rest = await collectEvents(env.host, 'tb-appr', isTurnEnd, { after: lastCursor(asked) })
      assert.equal(turnEndReason(rest), 'completed')
      assert.equal(env.stub.executions().length, outcome === 'allowed-once' ? 1 : 0)
    } finally {
      await env.stop()
    }
  }
})

test('业务服务判定执行失败：模型拿到错误结果，回合照常结束', async () => {
  const env = await startBridgeHost(
    { sequence: ['tool_call_success', 'success'], toolName: 'kw_biz_a', toolArguments: JSON.stringify({ q: 'x' }) },
    { [TOKEN_A]: [bizTool('kw_biz_a')] },
    () => ({ status: 403, body: { error: { code: 'tool_not_available', message: '工具 kw_biz_a 已不在本会话的可用范围内' } } }),
  )
  try {
    await env.host.call('POST', '/v1/sessions', { sessionId: 'tb-fail', tools: { token: TOKEN_A } })
    await env.host.call('POST', '/v1/sessions/tb-fail/messages', { text: '试试' })
    const frames = await collectEvents(env.host, 'tb-fail', isTurnEnd)
    assert.equal(turnEndReason(frames), 'completed')
    assert.ok(messagesText(env.host.llm.requests[1]).includes('已不在本会话的可用范围内'), '错误说明没有回到模型')
  } finally {
    await env.stop()
  }
})

test('失败关闭：拉取失败或与内置工具重名时会话打不开，状态不残留', async () => {
  const env = await startBridgeHost({ sequence: ['success'] }, { [TOKEN_A]: [bizTool('kw_biz_a')], [TOKEN_B]: [bizTool('kw_echo')] })
  try {
    const unknown = await env.host.call('POST', '/v1/sessions', { sessionId: 'tb-closed', tools: { token: 'x'.repeat(40) } })
    assert.equal(unknown.status, 400)
    assert.equal(unknown.body.error.code, 'session_open_failed')
    const clash = await env.host.call('POST', '/v1/sessions', { sessionId: 'tb-clash', tools: { token: TOKEN_B } })
    assert.equal(clash.status, 400, '与内置工具重名的业务工具没有被拒绝')
    assert.equal((await env.host.call('POST', '/v1/sessions', { sessionId: 'tb-closed', tools: { token: TOKEN_A } })).status, 201)
    assert.equal((await env.host.call('POST', '/v1/sessions', { sessionId: 'tb-short', tools: { token: 'short' } })).status, 400)
  } finally {
    await env.stop()
  }
})

test('令牌续期：下一次工具调用使用新令牌；未启用工具桥的会话拒绝续期', async () => {
  const env = await startBridgeHost(
    { sequence: ['tool_call_success', 'success'], toolName: 'kw_biz_a', toolArguments: JSON.stringify({ q: 'x' }) },
    { [TOKEN_A]: [bizTool('kw_biz_a')], [TOKEN_B]: [bizTool('kw_biz_a')] },
  )
  try {
    await env.host.call('POST', '/v1/sessions', { sessionId: 'tb-rot', tools: { token: TOKEN_A } })
    assert.equal((await env.host.call('PUT', '/v1/sessions/tb-rot/tool-credential', { token: TOKEN_B })).status, 200)
    await env.host.call('POST', '/v1/sessions/tb-rot/messages', { text: '调用' })
    await collectEvents(env.host, 'tb-rot', isTurnEnd)
    assert.equal(env.stub.executions()[0]?.token, TOKEN_B)
    await env.host.call('POST', '/v1/sessions', { sessionId: 'tb-plain' })
    assert.equal((await env.host.call('PUT', '/v1/sessions/tb-plain/tool-credential', { token: TOKEN_B })).status, 409)
  } finally {
    await env.stop()
  }
})

test('审批按工具声明的 approval 决定：never 不问直接执行（风险照旧），ask 即使只读也要问', async () => {
  const cases = [
    { tool: { ...bizTool('kw_box_exec', 'dangerous'), approval: 'never' }, asks: false },
    { tool: { ...bizTool('kw_strict_read', 'read'), approval: 'ask' }, asks: true },
  ]
  for (const { tool, asks } of cases) {
    const env = await startBridgeHost(
      { sequence: ['tool_call_success', 'success'], toolName: tool.name, toolArguments: JSON.stringify({ q: 'x' }) },
      { [TOKEN_A]: [tool] },
    )
    try {
      await env.host.call('POST', '/v1/sessions', { sessionId: 'tb-mode', tools: { token: TOKEN_A } })
      await env.host.call('POST', '/v1/sessions/tb-mode/messages', { text: '跑一下' })
      const frames = await collectEvents(env.host, 'tb-mode', f => isTurnEnd(f) || f.kind === 'approval/requested')
      assert.equal(frames.some(f => f.kind === 'approval/requested'), asks, `${tool.name} 的审批行为不对`)
      if (!asks) {
        assert.equal(turnEndReason(frames), 'completed')
        assert.equal(env.stub.executions().length, 1)
      } else {
        assert.equal(env.stub.executions().length, 0, '审批之前就执行了')
      }
    } finally {
      await env.stop()
    }
  }
})

test('不合法的审批方式、过长的使用规则或认不得的回答方式：会话打不开（失败关闭）', async () => {
  const TOKEN_C = 'tools-token-c-'.padEnd(40, 'c')
  const env = await startBridgeHost({ sequence: ['success'] }, {
    [TOKEN_A]: [{ ...bizTool('kw_biz_a', 'write'), approval: 'sometimes' }],
    [TOKEN_B]: [{ ...bizTool('kw_biz_b'), guidance: 'x'.repeat(1001) }],
    [TOKEN_C]: [{ ...bizTool('kw_biz_c'), answerModes: ['quick', 'fast'] }],
  })
  try {
    assert.equal((await env.host.call('POST', '/v1/sessions', { sessionId: 'tb-bad-a', tools: { token: TOKEN_A } })).status, 400)
    assert.equal((await env.host.call('POST', '/v1/sessions', { sessionId: 'tb-bad-b', tools: { token: TOKEN_B } })).status, 400)
    assert.equal((await env.host.call('POST', '/v1/sessions', { sessionId: 'tb-bad-c', tools: { token: TOKEN_C } })).status, 400)
  } finally {
    await env.stop()
  }
})

test('工具的使用规则进系统提示：同一条只写一次，被策略禁用的工具的规则不出现', async () => {
  const shared = '交付规则：文件写到 output 顶层'
  const env = await startBridgeHost({ sequence: ['success', 'success'] }, {
    [TOKEN_A]: [
      { ...bizTool('kw_box_a'), guidance: shared },
      { ...bizTool('kw_box_b'), guidance: shared },
      { ...bizTool('kw_box_c'), guidance: '只属于 C 的规则' },
    ],
  })
  try {
    await env.host.call('POST', '/v1/sessions', { sessionId: 'tb-guide', tools: { token: TOKEN_A } })
    await env.host.call('POST', '/v1/sessions', { sessionId: 'tb-guide-deny', policy: { deny: ['kw_box_c'] }, tools: { token: TOKEN_A } })
    for (const id of ['tb-guide', 'tb-guide-deny']) {
      await env.host.call('POST', `/v1/sessions/${id}/messages`, { text: `会话 ${id}` })
      await collectEvents(env.host, id, isTurnEnd)
    }
    const requestOf = id => env.host.llm.requests.find(r => messagesText(r).includes(`会话 ${id}`))
    const full = systemOf(requestOf('tb-guide'))
    assert.equal(full.split(shared).length - 1, 1, '同一条规则写了不止一次')
    assert.ok(full.includes('只属于 C 的规则'))
    const denied = systemOf(requestOf('tb-guide-deny'))
    assert.ok(denied.includes(shared), '没被禁用的工具的规则应当还在')
    assert.ok(!denied.includes('只属于 C 的规则'), '被禁用工具的规则出现在系统提示里')
  } finally {
    await env.stop()
  }
})

test('随工具清单来的说明（例如还没连接的服务）也写进系统提示，没有工具规则时同样写', async () => {
  const notes = '这些服务要用户先连接自己的账号才能用：GitLab。用户要做的事用得上它们时，告诉他到「设置 → 连接」里连接。'
  const env = await startBridgeHost({ sequence: ['success'] }, { [TOKEN_A]: [bizTool('kw_plain')] }, undefined, notes)
  try {
    await env.host.call('POST', '/v1/sessions', { sessionId: 'tb-notes', tools: { token: TOKEN_A } })
    await env.host.call('POST', '/v1/sessions/tb-notes/messages', { text: '帮我看看 GitLab 上的合并请求' })
    await collectEvents(env.host, 'tb-notes', isTurnEnd)
    const request = env.host.llm.requests.find(r => messagesText(r).includes('GitLab 上的合并请求'))
    assert.ok(systemOf(request).includes('设置 → 连接'), '没连接的服务的说明没进系统提示')
  } finally {
    await env.stop()
  }
})

test('业务服务声明 concurrent 的工具：同一步里的几个调用同时执行；没声明的照旧一个一个来', async () => {
  const gateway = await startOpenAiStub({ script: [
    { calls: [{ tool: 'kw_fast', args: { q: '一' } }, { tool: 'kw_fast', args: { q: '二' } }] },
    { text: '好了' },
    { calls: [{ tool: 'kw_slow', args: { q: '一' } }, { tool: 'kw_slow', args: { q: '二' } }] },
    { text: '好了' },
  ] })
  const spans = []
  const stub = await startToolsStub({
    toolsByToken: { [TOKEN_A]: [{ ...bizTool('kw_fast'), concurrent: true }, bizTool('kw_slow')] },
    execute: async call => {
      const span = { tool: call.body.toolName, start: Date.now() }
      await new Promise(resolve => setTimeout(resolve, 400))
      spans.push({ ...span, end: Date.now() })
      return undefined
    },
  })
  const host = await startHost({ sequence: ['success'] }, {
    KNOWWORK_MODEL_PROVIDER: 'knowwork-gateway', KNOWWORK_MODEL: 'kw-chat', KNOWWORK_MODEL_BASE_URL: gateway.baseURL,
    KNOWWORK_MODEL_TOKEN: 'gw-token-parallel-0001-aaaaa', KNOWWORK_TOOLS_BASE_URL: stub.baseUrl,
  })
  try {
    await host.call('POST', '/v1/sessions', { sessionId: 'tb-par', tools: { token: TOKEN_A } })
    let after = 0
    for (const text of ['一起查', '慢慢查']) {
      await host.call('POST', '/v1/sessions/tb-par/messages', { text })
      after = lastCursor(await collectEvents(host, 'tb-par', isTurnEnd, { after }))
    }
    const overlap = name => {
      const [a, b] = spans.filter(s => s.tool === name).sort((x, y) => x.start - y.start)
      return b.start < a.end
    }
    assert.equal(overlap('kw_fast'), true, '声明 concurrent 的两个调用没有同时执行')
    assert.equal(overlap('kw_slow'), false, '没声明的工具不该同时执行')
  } finally {
    await host.stop(); await gateway.close(); await stub.close()
  }
})
