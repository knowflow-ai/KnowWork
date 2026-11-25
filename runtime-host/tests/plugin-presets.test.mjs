/**
 * 预置插件按会话生效（plugin-presets.mjs，官方 Agent 预设）：真实 DSH 组合 + 业务网关桩 + 业务工具桩。
 * - 合同里带了哪些插件，这段会话的模型请求里才有它们的工具；同一个宿主里别的会话没有；业务工具都在；
 * - 同组合的会话共用预设，几段会话同时开不撞车；
 * - 宿主重启后恢复：按合同重新装，工具和历史都在；恢复时清单变了就按新的装；
 * - 官方子 Agent 接到父会话的预设；
 * - 不认识的插件键：开会话失败并说明是哪个；
 * - 会话策略的 deny 收不掉预设里的工具（所以可用性只由清单决定），全局工具照样收得掉。
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { startHost, collectEvents, isTurnEndAfter, freshDshHome } from './support/harness.mjs'
import { startOpenAiStub } from './support/openai-stub.mjs'
import { bizTool, startToolsStub } from './support/tools-stub.mjs'

const OVERLAY = join(dirname(fileURLToPath(import.meta.url)), 'overlays', 'plugin-presets.yml')
const TOKEN = 'preset-tools-token-'.padEnd(40, 'p')
const DELEGATION = 'Your final answer is delivered automatically'
const names = request => (request.body.tools ?? []).map(tool => tool.function?.name)
const chat = gateway => gateway.requests.filter(r => r.path.endsWith('/chat/completions'))
const endOf = frames => frames.filter(f => f.kind === 'session/event').map(f => f.data).filter(e => e.type === 'turn/end').at(-1)?.data

async function boot(gateway, tools, home) {
  return startHost({ sequence: ['success'] }, {
    KNOWWORK_MODEL_PROVIDER: 'knowwork-gateway', KNOWWORK_MODEL: 'kw-chat',
    KNOWWORK_MODEL_BASE_URL: gateway.baseURL, KNOWWORK_MODEL_TOKEN: 'gw-token-preset-0001-aaaaaaaa',
    KNOWWORK_TOOLS_BASE_URL: tools.baseUrl,
  }, { patches: [OVERLAY], ...home === undefined ? {} : { home } })
}

async function open(host, sessionId, extra = {}) {
  return host.call('POST', '/v1/sessions', { sessionId, tools: { token: TOKEN }, ...extra })
}

async function turn(host, id, text, after = 0) {
  const sent = await host.call('POST', `/v1/sessions/${id}/messages`, { text })
  assert.equal(sent.status, 202, JSON.stringify(sent.body))
  return collectEvents(host, id, isTurnEndAfter(text), { after, timeoutMs: 60_000 })
}

test('带了哪些插件，这段会话才有它们的工具；同一个宿主里别的会话没有', async () => {
  const gateway = await startOpenAiStub({ script: [{ text: '好的' }] })
  const tools = await startToolsStub({ toolsByToken: { [TOKEN]: [bizTool('kw_biz_lookup')] } })
  const host = await boot(gateway, tools)
  try {
    const sessions = { 'p-probe': ['probe'], 'p-goal': ['goal'], 'p-both': ['goal', 'probe'], 'p-both-2': ['probe', 'goal'], 'p-none': undefined }
    // 同一时刻开：两段组合相同的会话要共用一次预设登记
    const opened = await Promise.all(Object.entries(sessions).map(([id, plugins]) => open(host, id, plugins === undefined ? {} : { plugins })))
    opened.forEach(r => assert.ok(r.status < 300, `${JSON.stringify(r.body)}\n${host.stderr().slice(-800)}`))
    const frames = await Promise.all(Object.keys(sessions).map(id => turn(host, id, `我是 ${id}。`)))
    frames.forEach(f => assert.equal(endOf(f)?.reason?.kind, 'completed'))
    const seen = id => names(chat(gateway).find(r => JSON.stringify(r.body.messages).includes(`我是 ${id}。`)))
    assert.ok(seen('p-probe').includes('kw_preset_probe') && !seen('p-probe').includes('create_goal'))
    assert.ok(seen('p-goal').includes('create_goal') && !seen('p-goal').includes('kw_preset_probe'))
    for (const id of ['p-both', 'p-both-2']) assert.ok(seen(id).includes('create_goal') && seen(id).includes('kw_preset_probe'), id)
    assert.ok(!seen('p-none').includes('create_goal') && !seen('p-none').includes('kw_preset_probe'))
    for (const id of Object.keys(sessions)) assert.ok(seen(id).includes('kw_biz_lookup'), `${id} 的业务工具还在`)
  } finally {
    await host.stop()
    await gateway.close()
    await tools.close()
  }
})

test('宿主重启后恢复：按合同重新装；恢复时清单变了就按新的装，历史都在', async () => {
  const gateway = await startOpenAiStub({ script: [{ text: '第一轮回答' }, { text: '第二轮回答' }, { text: '第三轮回答' }] })
  const tools = await startToolsStub({ toolsByToken: { [TOKEN]: [bizTool('kw_biz_lookup')] } })
  const home = freshDshHome()
  let host = await boot(gateway, tools, home)
  try {
    assert.ok((await open(host, 'p-resume', { plugins: ['goal'] })).status < 300)
    await turn(host, 'p-resume', '第一问')
    await host.stop({ keepHome: true })

    host = await boot(gateway, tools, home)
    assert.ok((await open(host, 'p-resume', { resume: true, policy: {}, plugins: ['goal'] })).status < 300, host.stderr().slice(-800))
    await turn(host, 'p-resume', '第二问')
    await host.stop({ keepHome: true })

    host = await boot(gateway, tools, home)
    assert.ok((await open(host, 'p-resume', { resume: true, policy: {} })).status < 300, host.stderr().slice(-800))
    assert.equal(endOf(await turn(host, 'p-resume', '第三问'))?.reason?.kind, 'completed')

    const [first, second, third] = chat(gateway)
    assert.ok(names(first).includes('create_goal'))
    assert.ok(names(second).includes('create_goal'), '重启恢复后预设里的工具还在')
    assert.match(JSON.stringify(second.body.messages), /第一轮回答/)
    assert.ok(!names(third).includes('create_goal'), '恢复时清单里没有它了，工具跟着没了')
    assert.match(JSON.stringify(third.body.messages), /第二轮回答/, '历史不受影响')
  } finally {
    await host.stop()
    await gateway.close()
    await tools.close()
  }
})

test('官方子 Agent 接到父会话的预设', async () => {
  const counts = { parent: 0 }
  const gateway = await startOpenAiStub({
    pick: body => {
      if (JSON.stringify(body).includes(DELEGATION)) return { text: '子任务完成' }
      return counts.parent++ === 0 ? { tool: 'subagent', args: { description: '子任务', prompt: '请处理子任务' } } : { text: '收到' }
    },
  })
  const tools = await startToolsStub({ toolsByToken: { [TOKEN]: [bizTool('kw_biz_lookup')] } })
  const host = await boot(gateway, tools)
  try {
    assert.ok((await open(host, 'p-parent', { plugins: ['probe'] })).status < 300)
    await host.call('POST', '/v1/sessions/p-parent/messages', { text: '交给子 Agent' })
    await collectEvents(host, 'p-parent', f => f.kind === 'subagent/event' && f.data.event?.type === 'turn/end', { timeoutMs: 30_000 })
    const child = chat(gateway).find(r => JSON.stringify(r.body).includes(DELEGATION))
    assert.ok(child, '子 Agent 没有发出模型请求')
    assert.ok(names(child).includes('kw_preset_probe') && names(child).includes('kw_biz_lookup'))
  } finally {
    await host.stop()
    await gateway.close()
    await tools.close()
  }
})

test('不认识的插件键开不了会话；deny 收不掉预设里的工具、收得掉全局工具', async () => {
  const gateway = await startOpenAiStub({ script: [{ text: '好的' }] })
  const tools = await startToolsStub({ toolsByToken: { [TOKEN]: [bizTool('kw_biz_lookup')] } })
  const host = await boot(gateway, tools)
  try {
    const unknown = await open(host, 'p-unknown', { plugins: ['nope'] })
    assert.equal(unknown.status, 400)
    assert.equal(unknown.body.error.code, 'plugin_unknown')
    assert.match(unknown.body.error.message, /nope/)
    assert.equal((await open(host, 'p-bad', { plugins: ['Bad Key'] })).body.error.code, 'invalid_request')
    assert.ok((await open(host, 'p-unknown')).status < 300, '失败以后同一个会话还能照常打开')

    assert.ok((await open(host, 'p-deny', { plugins: ['probe'], policy: { deny: ['kw_preset_probe', 'kw_echo'] } })).status < 300)
    await turn(host, 'p-deny', '你好')
    const seen = names(chat(gateway).at(-1))
    assert.ok(seen.includes('kw_preset_probe'), '预设里的工具不受 deny 影响：可用性只由清单决定')
    assert.ok(!seen.includes('kw_echo'), '全局工具照样被 deny 收掉')
  } finally {
    await host.stop()
    await gateway.close()
    await tools.close()
  }
})
