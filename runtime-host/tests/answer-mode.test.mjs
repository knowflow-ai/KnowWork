/**
 * 回答方式（answer-mode）：每条消息带「智能体 / 快速问答」，会话中途可以切换，照官方计划模式的形态：
 * 系统提示与工具清单不随切换变化，快速问答只在提问后面多一段指令，执行时只放行知识库检索；
 * 生效点是消息进入步骤时，排队等下一轮的消息不改正在跑的这一轮。
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { startHost, collectEvents, isTurnEnd } from './support/harness.mjs'
import { bizTool, startToolsStub } from './support/tools-stub.mjs'

const TOKEN = 'answer-mode-token-'.padEnd(40, 'a')
const RULE = '本轮是快速问答'
const REJECTED = '快速问答模式只能检索知识库'

/** 模型读得到的部分：系统提示与消息（官方模拟服务另带的 dsh_session_log 不算）。 */
const modelInput = (host, index) => {
  const body = host.llm.requests[index]?.body ?? {}
  return JSON.stringify({ system: body.system, messages: body.messages })
}
const toolNamesOf = request => (request.body?.tools ?? []).map(t => t.name).sort()
const turnEnds = count => {
  let seen = 0
  return frame => isTurnEnd(frame) && ++seen >= count
}

test('快速问答：提问后面多一段只检索的指令，切回智能体就没有；系统提示与工具清单前后一字不变', async () => {
  const host = await startHost({ sequence: ['success'], successText: '好' })
  try {
    await host.call('POST', '/v1/sessions', { sessionId: 'am-switch' })
    const sent = await host.call('POST', '/v1/sessions/am-switch/messages', { text: '报销标准是多少？', answerMode: 'quick' })
    assert.equal(sent.status, 202, JSON.stringify(sent.body))
    await collectEvents(host, 'am-switch', isTurnEnd)
    const quick = modelInput(host, 0)
    assert.ok(quick.lastIndexOf(RULE) > quick.indexOf('报销标准是多少？'), '快速问答的指令应当在提问之后')
    assert.ok(!JSON.stringify(host.llm.requests[0].body.system).includes(RULE), '系统提示里不应有模式指令')

    await host.call('POST', '/v1/sessions/am-switch/messages', { text: '帮我整理成表格' })
    await collectEvents(host, 'am-switch', turnEnds(2))
    const agent = modelInput(host, 1)
    assert.ok(agent.lastIndexOf(RULE) < agent.indexOf('帮我整理成表格'), '切回智能体后提问后面不应再有快速问答指令')
    assert.deepEqual(host.llm.requests[1].body.system, host.llm.requests[0].body.system, '切换模式改了系统提示')
    assert.deepEqual(toolNamesOf(host.llm.requests[1]), toolNamesOf(host.llm.requests[0]), '切换模式改了工具清单')
  } finally {
    await host.stop()
  }
})

test('快速问答：除了知识库检索，别的工具调用一律拦下，模型拿到原因', async () => {
  const host = await startHost({ sequence: ['tool_call_success', 'success'], toolName: 'kw_echo', toolArguments: JSON.stringify({ text: '越界' }) })
  try {
    await host.call('POST', '/v1/sessions', { sessionId: 'am-guard' })
    await host.call('POST', '/v1/sessions/am-guard/messages', { text: '随便试试', answerMode: 'quick' })
    await collectEvents(host, 'am-guard', isTurnEnd)
    const after = modelInput(host, 1)
    assert.ok(after.includes(REJECTED), '被拦下的调用没有把原因交给模型')
    assert.ok(!after.includes('echo: 越界'), '快速问答模式下别的工具被执行了')
  } finally {
    await host.stop()
  }
})

test('快速问答：知识库检索照常执行', async () => {
  const stub = await startToolsStub({ toolsByToken: { [TOKEN]: [bizTool('knowledge_search')] } })
  const host = await startHost(
    { sequence: ['tool_call_success', 'success'], toolName: 'knowledge_search', toolArguments: JSON.stringify({ q: '报销' }) },
    { KNOWWORK_TOOLS_BASE_URL: stub.baseUrl },
  )
  try {
    await host.call('POST', '/v1/sessions', { sessionId: 'am-search', tools: { token: TOKEN } })
    await host.call('POST', '/v1/sessions/am-search/messages', { text: '报销标准', answerMode: 'quick' })
    await collectEvents(host, 'am-search', isTurnEnd)
    assert.equal(stub.executions().length, 1, '知识库检索没有回调业务服务')
    assert.ok(!modelInput(host, 1).includes(REJECTED), '知识库检索被拦下了')
  } finally {
    await host.stop()
    await stub.close()
  }
})

test('排队等下一轮的快速问答消息不改正在跑的这一轮：这一轮的工具照常执行，下一轮才按快速问答', async () => {
  // 第一轮（智能体）连调两次工具再回答；第二条消息在第一轮开始后立刻排队（每轮只领一条排队消息，必定落到第二轮）
  const host = await startHost({
    sequence: ['tool_call_success', 'tool_call_success', 'success'],
    toolName: 'kw_echo',
    toolArguments: JSON.stringify({ text: '照常' }),
  })
  try {
    await host.call('POST', '/v1/sessions', { sessionId: 'am-queue' })
    await host.call('POST', '/v1/sessions/am-queue/messages', { text: '先做一件事', answerMode: 'agent' })
    await host.call('POST', '/v1/sessions/am-queue/messages', { text: '再问个知识库的问题', answerMode: 'quick' })
    await collectEvents(host, 'am-queue', turnEnds(2))
    const firstTurnEnd = modelInput(host, 2)
    assert.equal(firstTurnEnd.split('echo: 照常').length - 1, 2, '智能体回合的两次工具调用没有都执行')
    assert.ok(!firstTurnEnd.includes(REJECTED), '排队的快速问答消息把正在跑的智能体回合拦下了')
    assert.ok(!firstTurnEnd.includes(RULE), '正在跑的智能体回合里出现了快速问答指令')
    const second = modelInput(host, 3)
    assert.ok(second.lastIndexOf(RULE) > second.indexOf('再问个知识库的问题'), '第二轮没有按快速问答')
  } finally {
    await host.stop()
  }
})

test('回答方式只能是 agent 或 quick', async () => {
  const host = await startHost({ sequence: ['success'], successText: '好' })
  try {
    await host.call('POST', '/v1/sessions', { sessionId: 'am-bad' })
    const bad = await host.call('POST', '/v1/sessions/am-bad/messages', { text: '你好', answerMode: 'fast' })
    assert.equal(bad.status, 400)
  } finally {
    await host.stop()
  }
})
