// 收合同（intake.mjs）：两种宿主共用的那段。真起宿主的整轮在 smoke、answer-mode、turn-context 等测试里，这里只钉住
// 合同怎么变成会话状态与这一轮的消息（Agent 用替身）。
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { HostError, createTurnIntake, provision, sessionContractOf } from '../plugins/dsh-host/src/intake.mjs'
import { MENTION_PREFIX } from '../plugins/dsh-host/src/mentions.mjs'
import { dropSession, getSession } from '../plugins/dsh-host/src/state.mjs'

const TOKEN = 't'.repeat(40)

function fakeAgent(current = { provider: 'gw', model: 'default' }) {
  const sent = []
  return {
    sent,
    model: { current: () => current, select: (choice) => sent.push(['select', choice]) },
    followup: (message) => sent.push(['followup', message]),
    steer: (message) => sent.push(['steer', message]),
  }
}

const intake = (known = ['default', 'fast']) =>
  createTurnIntake({ models: { knows: (id) => known.includes(id) }, provider: 'gw', fallbackTimeZone: 'Asia/Shanghai' })

test('开会话的合同建成会话状态：策略、令牌、属主都在；恢复会话必须带策略；格式不对拒收', () => {
  const contract = sessionContractOf({
    sessionId: 'c_intake1', resume: true, policy: { deny: ['bash'], gate: { requiredTool: 'show_result', maxNudges: 2 } },
    tools: { token: TOKEN }, owner: { workspaceId: 'ws', userId: 'u1' },
  })
  const state = provision(contract)
  try {
    assert.equal(getSession('c_intake1'), state)
    assert.deepEqual(state.policy, { deny: ['bash'], gate: { requiredTool: 'show_result', maxNudges: 2 } })
    assert.deepEqual([state.toolAccess, state.skillAccess, state.owner], [{ token: TOKEN }, undefined, { workspaceId: 'ws', userId: 'u1' }])
  } finally {
    dropSession(state)
  }
  assert.throws(() => sessionContractOf({ sessionId: 'c_1', resume: true }), (e) => e instanceof HostError && e.code === 'policy_required')
  for (const bad of [{ sessionId: 'a b' }, { sessionId: 'c_1', tools: { token: 'short' } }, { sessionId: 'c_1', owner: { userId: 'u' } },
    { sessionId: 'c_1', policy: { deny: 'bash' } }, { sessionId: 'c_1', policy: { gate: { maxNudges: 9 } } }]) {
    assert.throws(() => sessionContractOf(bad), (e) => e instanceof HostError && e.status === 400, JSON.stringify(bad))
  }
})

test('这一轮的合同写成用户消息：原话后面跟 @ 的说明块；语言、时区、目录、回答方式记进会话状态', () => {
  const state = provision(sessionContractOf({ sessionId: 'c_intake2' }))
  try {
    const agent = fakeAgent()
    const result = intake()(agent, state, {
      text: '报销标准', language: 'zh', timeZone: 'Asia/Tokyo', answerMode: 'quick',
      knowledge: { bases: [{ id: 'builtin.kb1', name: '制度库', description: '' }], omitted: 0 },
      mentions: [{ type: 'knowledge_base', id: 'builtin.kb1', name: '制度库' }],
    })
    const [[how, message]] = agent.sent
    assert.equal(how, 'followup')
    assert.equal(result.messageId, message.id)
    assert.equal(message.content[0].text, '报销标准')
    assert.ok(message.content[1].text.startsWith(MENTION_PREFIX), '@ 的说明块紧跟原话（业务服务投影据此显示小标签）')
    assert.equal(message.source.clientTimeZone, 'Asia/Tokyo')
    assert.deepEqual([state.language, state.timeZone, state.knowledgeCatalog.bases[0].id], ['zh', 'Asia/Tokyo', 'builtin.kb1'])
    assert.deepEqual(state.answerModes.get(String(message.id)), { mode: 'quick', spec: undefined })
  } finally {
    dropSession(state)
  }
})

test('插话进正在跑的那一轮；选了别的模型按官方会话级选择换，宿主不认识的拒收（409 model_unknown）', () => {
  const agent = fakeAgent()
  const receive = intake()
  receive(agent, undefined, { text: '只看今年', mode: 'steer', model: 'fast' })
  assert.deepEqual(agent.sent.map(([how]) => how), ['select', 'steer'])
  assert.deepEqual(agent.sent[0][1], { provider: 'gw', model: 'fast' })
  assert.throws(() => receive(agent, undefined, { text: 'x', model: 'nope' }), (e) => e.status === 409 && e.code === 'model_unknown')
  assert.throws(() => receive(agent, undefined, { text: 'x', mode: 'later' }), (e) => e.status === 400)
  assert.throws(() => receive(agent, undefined, { text: '' }), (e) => e.code === 'invalid_request')
  assert.throws(() => receive(agent, undefined, { text: 'x', answerMode: 'custom' }), (e) => e.code === 'invalid_request',
    '扩展的回答方式要带放行清单与指令')
})
