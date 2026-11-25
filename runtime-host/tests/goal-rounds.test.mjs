/**
 * 长任务目标的自动续跑（官方 dsh-goal-round-driver 原样挂）和每轮开始前的核对（goal-gate.mjs）：
 * - 用 /goal 设了目标，智能体空下来就自己接着做；每一轮开始前都请业务服务核对（kind goal、对话归谁），通过才调模型；
 *   做到轮数上限就停，目标标成卡住；
 * - 业务服务不同意（额度用完）：不调模型，目标标成卡住，原因用额度那边的话；
 * - 每一轮发起的那条消息来源是 goal，带着第几轮（业务服务据此显示「长任务目标 · 第 n 轮」）。
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { startHost, collectEvents } from './support/harness.mjs'
import { startOpenAiStub } from './support/openai-stub.mjs'
import { bizTool, startToolsStub } from './support/tools-stub.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const OVERLAYS = [join(HERE, 'overlays', 'plugin-presets.yml'), join(HERE, 'overlays', 'goal-rounds.yml')]
const TOKEN = 'goal-rounds-token-'.padEnd(40, 'g')
const chat = gateway => gateway.requests.filter(r => r.path.endsWith('/chat/completions'))

/** 业务服务激活接口的替身：按会话决定同不同意，记下每次请求 */
async function startActivationStub(answer) {
  const calls = []
  const server = createServer(async (req, res) => {
    const chunks = []
    for await (const chunk of req) chunks.push(chunk)
    const body = JSON.parse(Buffer.concat(chunks).toString('utf8'))
    calls.push({ authorization: req.headers.authorization, body })
    const [status, reply] = answer(body)
    res.writeHead(status, { 'content-type': 'application/json' })
    res.end(JSON.stringify(reply))
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  return { baseUrl: `http://127.0.0.1:${server.address().port}/internal/host/v1`, calls, close: () => server.close() }
}

const goalOf = async (host, id) => (await host.call('POST', `/v1/sessions/${id}/commands`, { line: '/goal' })).body.text

test('设了目标就自己一轮轮接着做，每轮先请业务服务核对；到上限停下；业务服务不同意就不调模型、标成卡住', async () => {
  const gateway = await startOpenAiStub({ script: [{ text: '这一轮核对了一部分' }] })
  const tools = await startToolsStub({ toolsByToken: { [TOKEN]: [bizTool('kw_biz_lookup')] } })
  const activation = await startActivationStub(body => body.sessionId === 'g-broke'
    ? [429, { detail: { message: '本月个人额度已用完，12 月 1 日恢复' } }]
    : [200, { ok: true }])
  const host = await startHost({ sequence: ['success'] }, {
    KNOWWORK_MODEL_PROVIDER: 'knowwork-gateway', KNOWWORK_MODEL: 'kw-chat',
    KNOWWORK_MODEL_BASE_URL: gateway.baseURL, KNOWWORK_MODEL_TOKEN: 'gw-token-goal-0001-aaaaaaaa',
    KNOWWORK_TOOLS_BASE_URL: tools.baseUrl, KNOWWORK_ACTIVATION_BASE_URL: activation.baseUrl,
  }, { patches: OVERLAYS })
  try {
    const owner = { workspaceId: 'ws1', userId: 'u1' }
    for (const id of ['g-ok', 'g-broke']) {
      const opened = await host.call('POST', '/v1/sessions', { sessionId: id, tools: { token: TOKEN }, plugins: ['goal'], owner })
      assert.ok(opened.status < 300, JSON.stringify(opened.body))
    }

    const set = await host.call('POST', '/v1/sessions/g-ok/commands', { line: '/goal 把三份合同都核对一遍' })
    assert.equal(set.body.kind, 'success', JSON.stringify(set.body))
    // 两轮（测试叠加的上限）跑完：目标因为到上限被标成卡住
    const frames = await collectEvents(host, 'g-ok', (_frame, all) =>
      all.some(f => f.kind === 'session/event' && f.data?.type === 'goal/change' && f.data.data?.goal?.phase === 'blocked'),
    { timeoutMs: 60_000 })
    const rounds = frames.filter(f => f.kind === 'session/event' && f.data?.type === 'user/message' && f.data.data?.source?.kind === 'goal')
    assert.deepEqual(rounds.map(f => f.data.data.source.round), [1, 2])
    assert.equal(chat(gateway).length, 2, '两轮各调一次模型')
    assert.match(JSON.stringify(chat(gateway)[0].body.messages), /三份合同/, '每一轮把目标交给模型')
    const okCalls = activation.calls.filter(c => c.body.sessionId === 'g-ok')
    assert.ok(okCalls.length >= 2, '每一轮开始前都问了业务服务')
    assert.deepEqual(okCalls[0].body, { kind: 'goal', sessionId: 'g-ok', owner })
    assert.match(okCalls[0].authorization, /^Bearer /)
    assert.match(await goalOf(host, 'g-ok'), /Status: blocked/)
    // 自动续跑开关：照官方是进程内的帧、不补积压，业务服务现取——到上限卡住以后是关着的
    const state = await host.call('GET', '/v1/sessions/g-ok/goal')
    assert.equal(state.status, 200, JSON.stringify(state.body))
    assert.equal(state.body.goal.phase, 'blocked')
    assert.equal(state.body.goal.armed, false)

    const before = chat(gateway).length
    await host.call('POST', '/v1/sessions/g-broke/commands', { line: '/goal 把十份合同都核对一遍' })
    await collectEvents(host, 'g-broke', (_frame, all) =>
      all.some(f => f.kind === 'session/event' && f.data?.type === 'goal/change' && f.data.data?.goal?.phase === 'blocked'),
    { timeoutMs: 30_000 })
    assert.equal(chat(gateway).length, before, '业务服务不同意：不调模型')
    const shown = await goalOf(host, 'g-broke')
    assert.match(shown, /Status: blocked/)
    assert.match(shown, /额度用完了.*12 月 1 日恢复/, '卡住的原因写的是额度那边的话')
  } finally {
    await host.stop()
    await gateway.close()
    await tools.close()
    activation.close()
  }
})

test('个人专属宿主：业务服务下发了按人签的激活令牌，每轮核对带它，不带宿主令牌', async () => {
  const gateway = await startOpenAiStub({ script: [{ text: '这一轮做完了' }] })
  const tools = await startToolsStub({ toolsByToken: { [TOKEN]: [bizTool('kw_biz_lookup')] } })
  const activation = await startActivationStub(() => [200, { ok: true }])
  const host = await startHost({ sequence: ['success'] }, {
    KNOWWORK_MODEL_PROVIDER: 'knowwork-gateway', KNOWWORK_MODEL: 'kw-chat',
    KNOWWORK_MODEL_BASE_URL: gateway.baseURL, KNOWWORK_MODEL_TOKEN: 'gw-token-goal-0002-aaaaaaaa',
    KNOWWORK_TOOLS_BASE_URL: tools.baseUrl, KNOWWORK_ACTIVATION_BASE_URL: activation.baseUrl,
  }, { patches: OVERLAYS })
  const issued = 'activation-token-for-u1-'.padEnd(48, 'a')
  try {
    const rotated = await host.call('PUT', '/v1/activation-credential', { token: issued })
    assert.equal(rotated.status, 200, JSON.stringify(rotated.body))
    const opened = await host.call('POST', '/v1/sessions', {
      sessionId: 'g-own', tools: { token: TOKEN }, plugins: ['goal'], owner: { workspaceId: 'ws1', userId: 'u1' },
    })
    assert.ok(opened.status < 300, JSON.stringify(opened.body))
    await host.call('POST', '/v1/sessions/g-own/commands', { line: '/goal 核对一份合同' })
    await collectEvents(host, 'g-own', (_frame, all) =>
      all.some(f => f.kind === 'session/event' && f.data?.type === 'goal/change' && f.data.data?.goal?.phase === 'blocked'),
    { timeoutMs: 60_000 })
    assert.ok(activation.calls.length >= 1)
    assert.ok(activation.calls.every(call => call.authorization === `Bearer ${issued}`), '用下发的激活令牌，不是宿主令牌')
    assert.equal((await host.call('PUT', '/v1/activation-credential', { token: 'short' })).status, 400)
  } finally {
    await host.stop()
    await gateway.close()
    await tools.close()
    activation.close()
  }
})
