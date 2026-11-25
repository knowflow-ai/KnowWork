import { test } from 'node:test'
import assert from 'node:assert/strict'
import { startHost, collectEvents, isTurnEnd } from './support/harness.mjs'
import { startOpenAiStub } from './support/openai-stub.mjs'
import { canonicalTimeZone, userSource } from '../plugins/dsh-host/src/message-source.mjs'

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
