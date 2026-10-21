/**
 * E1 剩余项 1：模型走官方 `dsh-llm-pi-ai` 适配器，接业务服务的 OpenAI 兼容网关。
 * 网关由 openai-stub 扮演，记录宿主实际发出的请求头与请求体。
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { startHost, collectEvents, isTurnEnd } from './support/harness.mjs'
import { startOpenAiStub } from './support/openai-stub.mjs'

const sessionEvents = frames => frames.filter(f => f.kind === 'session/event').map(f => f.data)
const turnEndReason = frames => sessionEvents(frames).find(e => e.type === 'turn/end')?.data?.reason?.kind
const lastCursor = frames => frames.at(-1)?.cursor ?? 0

async function startGatewayHost(script, firstToken) {
  let expected = firstToken
  const gateway = await startOpenAiStub({ script, token: () => expected })
  const host = await startHost({ sequence: ['success'] }, {
    KNOWWORK_MODEL_PROVIDER: 'knowwork-gateway',
    KNOWWORK_MODEL: 'kw-chat',
    KNOWWORK_MODEL_BASE_URL: gateway.baseURL,
    KNOWWORK_MODEL_TOKEN: firstToken,
  })
  return {
    gateway,
    host,
    expect: token => { expected = token },
    stop: async () => { await host.stop(); await gateway.close() },
  }
}

test('经官方 pi-ai 适配器走 OpenAI 兼容网关：带令牌、流式、回答进入事件流', async () => {
  const env = await startGatewayHost([{ text: '这是网关返回的回答' }], 'gw-token-0001-aaaaaaaaaaaa')
  try {
    await env.host.call('POST', '/v1/sessions', { sessionId: 'gw-1' })
    await env.host.call('POST', '/v1/sessions/gw-1/messages', { text: '请回答' })
    const frames = await collectEvents(env.host, 'gw-1', isTurnEnd)
    assert.equal(turnEndReason(frames), 'completed', JSON.stringify(sessionEvents(frames).at(-1)))
    assert.ok(JSON.stringify(sessionEvents(frames)).includes('这是网关返回的回答'))
    const req = env.gateway.requests.find(r => r.path.endsWith('/chat/completions'))
    assert.ok(req, '网关没有收到请求')
    assert.equal(req.headers.authorization, 'Bearer gw-token-0001-aaaaaaaaaaaa')
    assert.equal(req.body.model, 'kw-chat')
    assert.equal(req.body.stream, true)
    assert.ok(JSON.stringify(req.body.messages).includes('请回答'))
    // 记录网关能否知道请求来自哪个会话（用于计量与审计）：打印实际情况，结论写进报告。
    const raw = JSON.stringify({ headers: req.headers, body: { ...req.body, messages: undefined, tools: undefined } })
    assert.equal(raw.includes('gw-1'), false, '网关已能看到会话标识，报告结论需要更新')
    // 网关看不到会话，计量改由宿主事件流承担：本轮的 token 用量必须出现在会话事件里。
    const assistant = sessionEvents(frames).find(e => e.type === 'assistant/message')
    const usage = assistant?.data?.usage
    assert.ok(usage && Object.values(usage).some(v => typeof v === 'number' && v > 0), `assistant/message 事件里没有用量：${JSON.stringify(usage)}`)
  } finally {
    await env.stop()
  }
})

test('经网关的工具调用：工具结果回传给网关的下一次请求', async () => {
  const env = await startGatewayHost([{ tool: 'kw_echo', args: { text: '你好' } }, { text: '工具已调用' }], 'gw-token-0002-aaaaaaaaaaaa')
  try {
    await env.host.call('POST', '/v1/sessions', { sessionId: 'gw-2' })
    await env.host.call('POST', '/v1/sessions/gw-2/messages', { text: '调用 kw_echo' })
    const frames = await collectEvents(env.host, 'gw-2', isTurnEnd)
    assert.equal(turnEndReason(frames), 'completed')
    const calls = env.gateway.requests.filter(r => r.path.endsWith('/chat/completions'))
    assert.equal(calls.length, 2)
    assert.ok((calls[0].body.tools ?? []).some(t => t.function?.name === 'kw_echo'), '网关请求里没有工具定义')
    assert.ok(JSON.stringify(calls[1].body.messages).includes('echo: 你好'), '工具结果没有回传给网关')
  } finally {
    await env.stop()
  }
})

test('令牌轮换：经官方凭据服务写入新令牌，下一次请求即生效，无需重启', async () => {
  const env = await startGatewayHost([{ text: '第一次' }, { text: '第二次' }], 'gw-token-0003-aaaaaaaaaaaa')
  try {
    await env.host.call('POST', '/v1/sessions', { sessionId: 'gw-3' })
    await env.host.call('POST', '/v1/sessions/gw-3/messages', { text: '一' })
    const first = await collectEvents(env.host, 'gw-3', isTurnEnd)
    assert.equal(turnEndReason(first), 'completed')

    env.expect('gw-token-0003-bbbbbbbbbbbb')
    const rotated = await env.host.call('PUT', '/v1/model-credential', { token: 'gw-token-0003-bbbbbbbbbbbb' })
    assert.equal(rotated.status, 200, JSON.stringify(rotated.body))
    await env.host.call('POST', '/v1/sessions/gw-3/messages', { text: '二' })
    const second = await collectEvents(env.host, 'gw-3', isTurnEnd, { after: lastCursor(first) })
    assert.equal(turnEndReason(second), 'completed', '轮换后的请求失败了')
    const accepted = env.gateway.requests.filter(r => r.path.endsWith('/chat/completions') && !r.rejected)
    assert.equal(accepted.at(-1).headers.authorization, 'Bearer gw-token-0003-bbbbbbbbbbbb')
  } finally {
    await env.stop()
  }
})
