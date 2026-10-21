/**
 * 技能交付与专业工具解锁（技术方案 6.1–6.3 节）：真实 DSH 组合 + 官方技能注册表与 `skill` 工具，
 * 业务服务的技能接口与工具接口由桩扮演，模型由 openai-stub 按脚本扮演。断言落到模型请求与桩收到的调用上。
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { startHost, collectEvents, isTurnEnd } from './support/harness.mjs'
import { startOpenAiStub } from './support/openai-stub.mjs'
import { publishedSkill, startSkillsStub } from './support/skills-stub.mjs'
import { bizTool, startToolsStub } from './support/tools-stub.mjs'

const SKILLS_TOKEN = 'skills-token-a-'.padEnd(40, 'a')
const TOOLS_TOKEN = 'tools-token-a-'.padEnd(40, 't')
const EXTRACT = publishedSkill('contract-extract', {
  body: '按字段抽取合同：先调用 extract_records，再给出表格。',
  unlocks: ['extract_records'],
  completionTools: ['extract_records'],
})
const NOTES = publishedSkill('meeting-notes', { body: '整理会议纪要的方法。' })

const toolNamesOf = request => (request.body?.tools ?? []).map(t => t.function?.name)
const messagesText = request => JSON.stringify(request.body?.messages ?? [])
const kinds = frames => frames.map(f => f.kind)

async function startSkillHost(script, { skills = [EXTRACT, NOTES], tamper = false, others = {} } = {}) {
  const gateway = await startOpenAiStub({ script })
  const skillsStub = await startSkillsStub({ skillsByToken: { [SKILLS_TOKEN]: skills, ...others }, tamper })
  const tools = await startToolsStub({ toolsByToken: { [TOOLS_TOKEN]: [bizTool('kw_biz_a'), bizTool('extract_records')] } })
  const host = await startHost({ sequence: ['success'] }, {
    KNOWWORK_MODEL_PROVIDER: 'knowwork-gateway',
    KNOWWORK_MODEL: 'kw-chat',
    KNOWWORK_MODEL_BASE_URL: gateway.baseURL,
    KNOWWORK_MODEL_TOKEN: 'gw-token-skills-0001-aaaaaaa',
    KNOWWORK_TOOLS_BASE_URL: tools.baseUrl,
    KNOWWORK_SKILLS_BASE_URL: skillsStub.baseUrl,
  })
  const chat = () => gateway.requests.filter(r => r.path.endsWith('/chat/completions'))
  return {
    host, skillsStub, tools, chat,
    open: (sessionId, extra = {}) => host.call('POST', '/v1/sessions', {
      sessionId, tools: { token: TOOLS_TOKEN }, skills: { token: SKILLS_TOKEN }, ...extra,
    }),
    stop: async () => { await host.stop(); await gateway.close(); await skillsStub.close(); await tools.close() },
  }
}

test('会话打开时按业务服务清单交给官方技能目录；没有业务技能的会话只看到官方随包技能', async () => {
  const env = await startSkillHost([{ text: '好的' }])
  try {
    assert.equal((await env.open('sk-cat')).status, 201)
    await env.host.call('POST', '/v1/sessions/sk-cat/messages', { text: '有哪些技能' })
    await collectEvents(env.host, 'sk-cat', isTurnEnd)
    await env.host.call('POST', '/v1/sessions', { sessionId: 'sk-none' })
    await env.host.call('POST', '/v1/sessions/sk-none/messages', { text: '没有技能的会话' })
    await collectEvents(env.host, 'sk-none', isTurnEnd)
    const [first, plain] = env.chat()
    assert.ok(toolNamesOf(first).includes('skill'), '有技能的会话看不到 skill 工具')
    assert.match(messagesText(first), /available_skills/)
    assert.match(messagesText(first), /contract-extract/)
    assert.match(messagesText(first), /meeting-notes/)
    assert.ok(!messagesText(first).includes('按字段抽取合同'), '目录里不该出现技能正文')
    // 没有业务技能的会话只看到官方随包技能（Office），看不到业务服务给别的会话的技能
    assert.ok(toolNamesOf(plain).includes('skill'), '官方随包技能也该能用 skill 工具加载')
    assert.match(messagesText(plain), /office-docx/)
    assert.ok(!messagesText(plain).includes('contract-extract'), '没有业务技能的会话看到了业务技能')
    assert.equal(env.skillsStub.requests.filter(r => r.path.endsWith('/catalog')).length, 1)
    assert.equal(env.skillsStub.contentLoads().length, 0, '没加载就拉了内容')
  } finally {
    await env.stop()
  }
})

test('同一运行时的两个会话各自只看到、只能加载自己的技能', async () => {
  const otherToken = 'skills-token-o-'.padEnd(40, 'o')
  const env = await startSkillHost([{ tool: 'skill', args: { name: 'contract-extract' } }, { text: '完成' }],
    { others: { [otherToken]: [NOTES] } })
  try {
    await env.host.call('POST', '/v1/sessions', { sessionId: 'sk-other', skills: { token: otherToken } })
    await env.host.call('POST', '/v1/sessions/sk-other/messages', { text: '只给 B 的问题' })
    const frames = await collectEvents(env.host, 'sk-other', isTurnEnd)
    const [first, second] = env.chat()
    assert.match(messagesText(first), /meeting-notes/)
    assert.ok(!messagesText(first).includes('contract-extract`'), '会话 B 的目录里出现了别人的技能')
    assert.ok(!kinds(frames).includes('skill/loaded'), '会话 B 加载到了不属于它的技能')
    assert.match(messagesText(second), /unknown or no longer available/)
    assert.equal(env.skillsStub.contentLoads().length, 0)
  } finally {
    await env.stop()
  }
})

test('专业工具加载技能前不可见；加载后解锁、正文校验通过、完成判据满足才放行', async () => {
  const env = await startSkillHost([
    { tool: 'skill', args: { name: 'contract-extract' } },
    { tool: 'extract_records', args: { q: '采购合同' } },
    { text: '抽取完成' },
  ])
  try {
    await env.open('sk-flow')
    await env.host.call('POST', '/v1/sessions/sk-flow/messages', { text: '把合同抽成表' })
    const frames = await collectEvents(env.host, 'sk-flow', isTurnEnd)
    const [beforeLoad, afterLoad] = env.chat()
    assert.ok(!toolNamesOf(beforeLoad).includes('extract_records'), '加载技能之前就露出了专业工具')
    assert.ok(toolNamesOf(beforeLoad).includes('kw_biz_a'), '未被认领的业务工具不该被藏起来')
    assert.ok(toolNamesOf(afterLoad).includes('extract_records'), '加载技能之后专业工具仍不可见')
    assert.ok(messagesText(afterLoad).includes('先调用 extract_records'), '技能正文没有交给模型')
    assert.deepEqual(env.skillsStub.contentLoads().map(r => r.query), [{ name: 'contract-extract', digest: EXTRACT.entry.digest }])
    assert.equal(env.skillsStub.contentLoads()[0].token, SKILLS_TOKEN)
    assert.equal(env.tools.executions().length, 1)
    const loaded = frames.find(f => f.kind === 'skill/loaded')
    assert.deepEqual(loaded?.data, { sessionId: 'sk-flow', skill: 'contract-extract', version: '1.0.0', mode: 'automatic', unlocked: ['extract_records'] })
    const passed = frames.find(f => f.kind === 'skill-gate/passed')
    assert.deepEqual(passed?.data.skills, ['contract-extract'])
    assert.ok(!kinds(frames).includes('skill-gate/nudged'))
  } finally {
    await env.stop()
  }
})

test('加载了技能却没调用完成工具：最终回答被拦下，补救用完后以失败结束', async () => {
  const env = await startSkillHost([
    { tool: 'skill', args: { name: 'contract-extract' } },
    { text: '我直接回答了' },
  ])
  try {
    await env.open('sk-gate', { policy: { gate: { maxNudges: 1 } } })
    await env.host.call('POST', '/v1/sessions/sk-gate/messages', { text: '把合同抽成表' })
    const frames = await collectEvents(env.host, 'sk-gate', isTurnEnd)
    assert.deepEqual(kinds(frames).filter(k => k.startsWith('skill-gate/')), ['skill-gate/nudged', 'skill-gate/failed'])
    const failed = frames.find(f => f.kind === 'skill-gate/failed')
    assert.deepEqual(failed.data.missing, [{ skill: 'contract-extract', tools: ['extract_records'] }])
    assert.match(messagesText(env.chat()[2]), /技能 contract-extract 还没完成/)
  } finally {
    await env.stop()
  }
})

test('加载没有完成判据的普通技能不触发技能门；没加载技能直接调用专业工具不会执行', async () => {
  const env = await startSkillHost([
    { tool: 'extract_records', args: { q: '越权' } },
    { tool: 'skill', args: { name: 'meeting-notes' } },
    { text: '纪要整理完了' },
  ])
  try {
    await env.open('sk-plain')
    await env.host.call('POST', '/v1/sessions/sk-plain/messages', { text: '整理纪要' })
    const frames = await collectEvents(env.host, 'sk-plain', isTurnEnd)
    assert.equal(env.tools.executions().length, 0, '没加载技能的专业工具被执行了')
    assert.ok(!toolNamesOf(env.chat()[2]).includes('extract_records'), '加载别的技能不该解锁这个专业工具')
    assert.ok(frames.some(f => f.kind === 'skill/loaded' && f.data.skill === 'meeting-notes'))
    assert.ok(!kinds(frames).some(k => k.startsWith('skill-gate/')), '普通技能不该触发技能门')
  } finally {
    await env.stop()
  }
})

test('技能正文与摘要不符时不交给模型，也不解锁专业工具', async () => {
  const env = await startSkillHost([
    { tool: 'skill', args: { name: 'contract-extract' } },
    { text: '加载失败了' },
  ], { tamper: true })
  try {
    await env.open('sk-tamper')
    await env.host.call('POST', '/v1/sessions/sk-tamper/messages', { text: '抽取' })
    const frames = await collectEvents(env.host, 'sk-tamper', isTurnEnd)
    const after = env.chat()[1]
    assert.ok(!messagesText(after).includes('被篡改'), '被篡改的正文交给了模型')
    assert.ok(!toolNamesOf(after).includes('extract_records'))
    assert.ok(!kinds(frames).includes('skill/loaded'))
  } finally {
    await env.stop()
  }
})

test('失败关闭：清单拉取失败会话打不开；清单不合格同样打不开', async () => {
  const bad = publishedSkill('bad-skill', { unlocks: ['extract_records'] })
  const env = await startSkillHost([{ text: 'x' }], { skills: [bad] })
  try {
    const unknown = await env.host.call('POST', '/v1/sessions', { sessionId: 'sk-x', skills: { token: 'z'.repeat(40) } })
    assert.equal(unknown.status, 400)
    assert.equal(unknown.body.error.code, 'session_open_failed')
    const invalid = await env.open('sk-invalid')
    assert.equal(invalid.status, 400, '解锁专业工具却没有完成判据的清单没有被拒绝')
    assert.equal((await env.host.call('POST', '/v1/sessions', { sessionId: 'sk-x', skills: { token: 'short' } })).status, 400)
  } finally {
    await env.stop()
  }
})

test('技能令牌续期：下一次加载使用新令牌；未启用技能的会话拒绝续期', async () => {
  const newToken = 'skills-token-b-'.padEnd(40, 'b')
  const env = await startSkillHost([{ tool: 'skill', args: { name: 'meeting-notes' } }, { text: '完成' }])
  try {
    await env.open('sk-rot')
    assert.equal((await env.host.call('PUT', '/v1/sessions/sk-rot/skill-credential', { token: newToken })).status, 200)
    await env.host.call('POST', '/v1/sessions/sk-rot/messages', { text: '整理纪要' })
    await collectEvents(env.host, 'sk-rot', isTurnEnd)
    assert.equal(env.skillsStub.contentLoads()[0]?.token, newToken)
    await env.host.call('POST', '/v1/sessions', { sessionId: 'sk-plain' })
    const refused = await env.host.call('PUT', '/v1/sessions/sk-plain/skill-credential', { token: newToken })
    assert.equal(refused.status, 409)
    assert.equal(refused.body.error.code, 'skills_not_enabled')
  } finally {
    await env.stop()
  }
})

test('用户显式调用 /技能名：官方注入正文，同样解锁专业工具并受完成判据约束', async () => {
  const env = await startSkillHost([
    { tool: 'extract_records', args: { q: '采购合同' } },
    { text: '抽取完成' },
  ])
  try {
    await env.open('sk-manual')
    await env.host.call('POST', '/v1/sessions/sk-manual/messages', { text: '/contract-extract 把合同抽成表' })
    const frames = await collectEvents(env.host, 'sk-manual', isTurnEnd)
    const [first] = env.chat()
    assert.ok(messagesText(first).includes('先调用 extract_records'), '显式调用没有注入技能正文')
    assert.ok(toolNamesOf(first).includes('extract_records'), '显式调用后专业工具仍不可见')
    assert.equal(env.tools.executions().length, 1)
    assert.ok(frames.some(f => f.kind === 'skill/loaded' && f.data.mode === 'manual'))
    assert.ok(frames.some(f => f.kind === 'skill-gate/passed'))
  } finally {
    await env.stop()
  }
})
