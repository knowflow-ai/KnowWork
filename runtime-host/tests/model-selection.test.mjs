/**
 * 按对话选模型：业务服务推可选的对话模型清单（PUT /v1/models），宿主在运行时换官方 pi-ai 路由的 models；
 * 发消息带 model 时按官方会话级选择换模型，之后的消息不带也照旧；宿主没收到过的模型直接拒。
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { startHost, collectEvents, isTurnEnd } from './support/harness.mjs'
import { startOpenAiStub } from './support/openai-stub.mjs'
import { withModels, modelListOf } from '../plugins/dsh-host/src/model-catalog.mjs'

const TOKEN = 'gw-token-model-select-aaaaaaaa'
const chatRequests = gateway => gateway.requests.filter(r => r.path.endsWith('/chat/completions'))

test('推来的清单换掉路由的模型（保留配置档里默认那条，它的 id 还是没求值的表达式），按能不能看图声明输入', () => {
  const expr = { __jsExpr: "process.env.KNOWWORK_MODEL ?? 'default'" }
  const current = { providers: { gw: { api: 'openai-completions', models: [
    { id: expr, name: '默认', input: ['text', 'image'] },
    { id: 'old', name: '旧的' },
  ] } }, other: 1 }
  const next = withModels(current, 'gw', 'default',
    modelListOf([{ id: 'pro', name: 'Pro', contextWindow: 64000, vision: false }, { id: 'eye', name: '看图', vision: true }]),
    131072)
  assert.deepEqual(next.providers.gw.models, [
    { id: expr, name: '默认', input: ['text', 'image'] },
    { id: 'pro', name: 'Pro', contextWindow: 64000, input: ['text'] },
    { id: 'eye', name: '看图', contextWindow: 131072, input: ['text', 'image'] },
  ])
  assert.equal(next.other, 1)
  assert.equal(current.providers.gw.models.length, 2, '不改原对象')
  for (const bad of [[{ id: 'a b', name: 'x' }], [{ id: 'a', name: '' }], [{ id: 'a', name: 'x' }, { id: 'a', name: 'y' }], 'x']) {
    assert.throws(() => modelListOf(bad))
  }
})

test('真实宿主：推清单后按对话选模型，之后的消息沿用；没推过的模型拒收', async () => {
  const gateway = await startOpenAiStub({ script: [{ text: '第一句' }, { text: '第二句' }, { text: '第三句' }], token: () => TOKEN })
  const host = await startHost({ sequence: ['success'] }, {
    KNOWWORK_MODEL_PROVIDER: 'knowwork-gateway',
    KNOWWORK_MODEL: 'kw-chat',
    KNOWWORK_MODEL_BASE_URL: gateway.baseURL,
    KNOWWORK_MODEL_TOKEN: TOKEN,
  })
  try {
    const bad = await host.call('PUT', '/v1/models', { models: [{ id: 'bad id', name: 'x' }] })
    assert.equal(bad.status, 400)
    // 不带上下文长度：宿主按配置档的默认窗口（131072）。小于官方压缩余量（65536）的窗口切过去会被压缩预检拒掉
    const synced = await host.call('PUT', '/v1/models', { models: [{ id: 'kw-pro', name: 'Pro' }] })
    assert.equal(synced.status, 200, JSON.stringify(synced.body))
    assert.deepEqual(synced.body, { models: 2 })

    await host.call('POST', '/v1/sessions', { sessionId: 'ms-1' })
    const unknown = await host.call('POST', '/v1/sessions/ms-1/messages', { text: '你好', model: 'kw-nope' })
    assert.equal(unknown.status, 409)
    assert.equal(unknown.body.error.code, 'model_unknown')

    await host.call('POST', '/v1/sessions/ms-1/messages', { text: '第一问' })
    let frames = await collectEvents(host, 'ms-1', isTurnEnd)
    assert.equal(chatRequests(gateway).at(-1).body.model, 'kw-chat', '没选时用配置档里的默认模型')

    await host.call('POST', '/v1/sessions/ms-1/messages', { text: '第二问', model: 'kw-pro' })
    frames = await collectEvents(host, 'ms-1', isTurnEnd, { after: frames.at(-1).cursor })
    assert.equal(chatRequests(gateway).at(-1).body.model, 'kw-pro', '选了就换')

    await host.call('POST', '/v1/sessions/ms-1/messages', { text: '第三问' })
    await collectEvents(host, 'ms-1', isTurnEnd, { after: frames.at(-1).cursor })
    assert.equal(chatRequests(gateway).at(-1).body.model, 'kw-pro', '之后不带也沿用这段对话选的')
  } finally {
    await host.stop()
    await gateway.close()
  }
})
