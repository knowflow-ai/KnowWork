import { test } from 'node:test'
import assert from 'node:assert/strict'
import { startHost, collectEvents, isTurnEnd } from './support/harness.mjs'
import { startOpenAiStub } from './support/openai-stub.mjs'
import { canonicalTimeZone, userSource, withTimeZone } from '../plugins/dsh-host/src/message-source.mjs'

/** 模型读得到的部分：系统提示与消息。 */
function modelInput(host, index = 0) {
  const body = host.llm.requests[index]?.body ?? {}
  return JSON.stringify({ system: body.system, messages: body.messages })
}

/** 发一条消息、等这一轮跑完，返回这一轮模型请求里看得到的内容。 */
async function ask(host, sessionId, body) {
  const before = host.llm.requests.length
  const sent = await host.call('POST', `/v1/sessions/${sessionId}/messages`, body)
  assert.equal(sent.status, 202, JSON.stringify(sent.body))
  await collectEvents(host, sessionId, frame => isTurnEnd(frame) && host.llm.requests.length > before)
  return modelInput(host, before)
}

test('官方时钟按业务服务带来的用户时区写当前时间，模型不用调工具就知道今天几号', async () => {
  const host = await startHost({ sequence: ['success'], successText: '好' }, { TZ: 'Asia/Tokyo' })
  try {
    await host.call('POST', '/v1/sessions', { sessionId: 'tz-1' })
    const input = await ask(host, 'tz-1', { text: '这份合同到期了吗', timeZone: 'America/New_York' })
    assert.match(input, /Time sampled while preparing turn 1, step 1: /, '没有时钟读数')
    assert.ok(input.includes('Browser time zone for this request: America/New_York.'), input.slice(-600))
    assert.ok(!input.includes('Ask the user to clarify'), '带了时区就不该让模型去问')

    // 别名先换成规范名再写进消息：官方时钟见到不规范的名字会报错，这一轮就答不出来
    await host.call('POST', '/v1/sessions', { sessionId: 'tz-1b' })
    const alias = await ask(host, 'tz-1b', { text: '今天几号', timeZone: 'US/Eastern' })
    assert.ok(alias.includes('Browser time zone for this request: America/New_York.'), alias.slice(-600))
  } finally {
    await host.stop()
  }
})

test('没带时区（IM、后台会话、工作流）用部署时区；写错的也换成部署时区，消息照收', async () => {
  const host = await startHost({ sequence: ['success'], successText: '好' }, { TZ: 'Asia/Tokyo' })
  try {
    await host.call('POST', '/v1/sessions', { sessionId: 'tz-2' })
    const none = await ask(host, 'tz-2', { text: '今天几号' })
    assert.ok(none.includes('Browser time zone for this request: Asia/Tokyo.'), none.slice(-600))
    assert.ok(!none.includes('unavailable. Ask the user'), '没带时区时不该让模型去问用户')

    await host.call('POST', '/v1/sessions', { sessionId: 'tz-3' })
    const bad = await ask(host, 'tz-3', { text: '今天几号', timeZone: 'Not/AZone' })
    assert.ok(bad.includes('Browser time zone for this request: Asia/Tokyo.'), bad.slice(-600))
  } finally {
    await host.stop()
  }
})

test('时区名照官方规则规范化：别名换成规范名（官方时钟见到别名会让那一步失败），不合规的不要', () => {
  assert.equal(canonicalTimeZone('Asia/Shanghai'), 'Asia/Shanghai')
  assert.equal(canonicalTimeZone('UTC'), 'UTC')
  assert.equal(canonicalTimeZone('US/Eastern'), 'America/New_York')
  assert.equal(canonicalTimeZone('Etc/UTC'), 'UTC')
  for (const bad of ['', ' Asia/Shanghai', 'asia', 'PRC', 'Not/AZone', '../etc/passwd', 8, undefined]) {
    assert.equal(canonicalTimeZone(bad), undefined, String(bad))
  }
  const source = userSource('US/Eastern', 'UTC')
  assert.equal(source.kind, 'user')
  assert.equal(source.clientTimeZone, 'America/New_York')
  assert.equal(typeof source.rpcId, 'string')
  assert.equal(userSource(undefined, 'Asia/Tokyo').clientTimeZone, 'Asia/Tokyo')
})

test('子 Agent 沿用根会话的时区：官方委派的任务消息不带时区，宿主补上，读数里不再让子 Agent 去问用户', async () => {
  // 官方给子 Agent 的首条消息里带「Your parent agent id is」，按它区分父子两边的请求
  const isChild = body => JSON.stringify(body).includes('Your parent agent id is')
  const counts = { parent: 0, child: 0 }
  const gateway = await startOpenAiStub({ pick: body => {
    const role = isChild(body) ? 'child' : 'parent'
    const index = counts[role]++
    if (role === 'parent' && index === 0) return { tool: 'subagent', args: { description: '核对期限', prompt: '算一下合同还有几天到期' } }
    return { text: role === 'parent' ? '好' : '还有 12 天' }
  } })
  const host = await startHost({ sequence: ['success'] }, {
    TZ: 'Asia/Tokyo',
    KNOWWORK_MODEL_PROVIDER: 'knowwork-gateway',
    KNOWWORK_MODEL: 'kw-chat',
    KNOWWORK_MODEL_BASE_URL: gateway.baseURL,
    KNOWWORK_MODEL_TOKEN: 'gw-token-tz-0001-aaaaaaaaaa',
  })
  try {
    await host.call('POST', '/v1/sessions', { sessionId: 'tz-sub' })
    await host.call('POST', '/v1/sessions/tz-sub/messages', { text: '交给子 Agent 核对', timeZone: 'America/New_York' })
    await collectEvents(host, 'tz-sub', f => f.kind === 'subagent/event' && f.data.event?.type === 'turn/end', { timeoutMs: 30_000 })
    const child = gateway.requests.find(r => r.path.endsWith('/chat/completions') && isChild(r.body))
    const input = JSON.stringify(child?.body?.messages ?? [])
    assert.ok(input.includes('Browser time zone for this request: America/New_York.'), input.slice(-800))
    assert.ok(!input.includes('unavailable. Ask the user'), '子 Agent 的读数还在让它去问用户')
  } finally {
    await host.stop()
    await gateway.close()
  }
})

test('只补没带时区的用户来源：已带编号的、父 Agent 发来的、系统补的上下文都不动', () => {
  const task = { id: 'm1', source: { kind: 'user' } }
  assert.deepEqual(withTimeZone(task, 'Asia/Shanghai').source.clientTimeZone, 'Asia/Shanghai')
  assert.equal(withTimeZone(task, 'Asia/Shanghai').id, 'm1')
  const direct = { id: 'm2', source: { kind: 'user', rpcId: 'r1', clientTimeZone: 'Europe/Paris' } }
  assert.equal(withTimeZone(direct, 'Asia/Shanghai'), direct)
  const fromParent = { id: 'm3', source: { kind: 'agent-message' } }
  assert.equal(withTimeZone(fromParent, 'Asia/Shanghai'), fromParent)
})

test('停用的 DeepSeek 专用插件不再往模型请求里加会话日志与插件包清单', async () => {
  const host = await startHost({ sequence: ['success'], successText: '好' })
  try {
    await host.call('POST', '/v1/sessions', { sessionId: 'tz-4' })
    await ask(host, 'tz-4', { text: '你好' })
    const body = host.llm.requests[0]?.body ?? {}
    assert.ok(!('dsh_session_log' in body), '会话日志还在往模型服务传')
    assert.ok(!('dsh_plugin_packages' in body), '插件包清单还在往模型服务传')
  } finally {
    await host.stop()
  }
})
