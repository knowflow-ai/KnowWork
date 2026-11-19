import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createTurnIntake, extensionStateOf } from '../plugins/dsh-host/src/intake.mjs'
import { createSession, dropSession } from '../plugins/dsh-host/src/state.mjs'

test('扩展的状态：按扩展名分的 JSON，名字不合规、太大整条拒收', () => {
  assert.equal(extensionStateOf(undefined), undefined)
  assert.deepEqual(extensionStateOf({ office: { documents: ['output/报告.docx'] } }), { office: { documents: ['output/报告.docx'] } })
  for (const bad of [[], 'x', { Office: {} }, { 'a b': {} }, { office: { blob: 'x'.repeat(70_000) } }]) {
    assert.throws(() => extensionStateOf(bad), undefined, JSON.stringify(bad).slice(0, 40))
  }
})

test('收下这一轮就记进会话状态（工具可见性在步骤开始前算）；插话、没带时沿用上一轮的', () => {
  const intake = createTurnIntake({ models: { knows: () => true }, provider: 'p', fallbackTimeZone: 'Asia/Shanghai' })
  const agent = { model: { current: () => undefined }, followup() {}, steer() {} }
  const state = createSession('ext-state-1', {})
  try {
    intake(agent, state, { text: '改一下', extensionState: { office: { documents: ['a.docx'] } } })
    assert.deepEqual(state.extensionState, { office: { documents: ['a.docx'] } })
    intake(agent, state, { text: '补一句', mode: 'steer', extensionState: { office: { documents: [] } } })
    assert.deepEqual(state.extensionState, { office: { documents: ['a.docx'] } }, '插话进的是正在跑的那一轮')
    intake(agent, state, { text: '再改' })
    assert.deepEqual(state.extensionState, { office: { documents: ['a.docx'] } }, '这一轮没算就沿用上一轮的')
    assert.throws(() => intake(agent, state, { text: 'x', extensionState: { Bad: 1 } }), /extensionState/)
  } finally {
    dropSession(state)
  }
})
