/**
 * 回答方式（answer-mode）：每条消息带「智能体 / 快速问答」，会话中途可以切换。快速问答那一步只给模型看声明了
 * 快速问答（answerModes 含 quick）的业务工具（官方条件工具 + restrict，模型看到的就是能执行的，不会调了才被拒），提问后面多一段指令；
 * 生效点是领取消息时，排队等下一轮的消息不改正在跑的这一轮。
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { startHost, collectEvents, isTurnEnd } from './support/harness.mjs'
import { bizTool, startToolsStub } from './support/tools-stub.mjs'

const TOKEN = 'answer-mode-token-'.padEnd(40, 'a')
const RULE = '本轮是快速问答'
const REJECTED = '快速问答模式不能调用'
/** 业务服务声明在快速问答里也提供的工具（如只读知识库工具） */
const quickTool = name => ({ ...bizTool(name), answerModes: ['agent', 'quick'] })

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

test('快速问答：提问后面多一段指令、工具只剩知识库的；切回智能体指令没了、工具全部回来', async () => {
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
    // 系统提示里不放模式指令；官方工具的使用说明跟着工具可见性走，快速问答收掉工具时那几段也不出现（不读用不了的工具的说明）
    assert.ok(!JSON.stringify(host.llm.requests[1].body.system).includes(RULE), '系统提示里不应有模式指令')
    // 这个会话没有知识库工具：快速问答那一步一个工具都不给；切回智能体，官方与宿主的工具都回来
    assert.deepEqual(toolNamesOf(host.llm.requests[0]), [], '快速问答那一步还给了模型别的工具')
    assert.ok(toolNamesOf(host.llm.requests[1]).includes('kw_echo'), '切回智能体后工具没有回来')
  } finally {
    await host.stop()
  }
})

test('快速问答：别的工具模型看不到；凭空调用也不执行，模型拿到原因', async () => {
  const host = await startHost({ sequence: ['tool_call_success', 'success'], toolName: 'kw_echo', toolArguments: JSON.stringify({ text: '越界' }) })
  try {
    await host.call('POST', '/v1/sessions', { sessionId: 'am-guard' })
    await host.call('POST', '/v1/sessions/am-guard/messages', { text: '随便试试', answerMode: 'quick' })
    await collectEvents(host, 'am-guard', isTurnEnd)
    assert.ok(!toolNamesOf(host.llm.requests[0]).includes('kw_echo'), '快速问答下模型还看得到别的工具')
    const after = modelInput(host, 1)
    assert.ok(after.includes(REJECTED), '被拦下的调用没有把原因交给模型')
    assert.ok(!after.includes('echo: 越界'), '快速问答模式下别的工具被执行了')
  } finally {
    await host.stop()
  }
})

for (const [toolName, args, label] of [
  ['knowledge_search', { q: '报销' }, '知识库检索'],
  // 一条规定跨了几个分块时要读全，只给检索片段会答一半
  ['knowledge_read', { source_id: 'builtin', doc_ref: 'doc_1', chunk: 'c_1' }, '知识库阅读'],
]) {
  test(`快速问答：${label}照常执行`, async () => {
    const stub = await startToolsStub({ toolsByToken: { [TOKEN]: [quickTool(toolName)] } })
    const host = await startHost(
      { sequence: ['tool_call_success', 'success'], toolName, toolArguments: JSON.stringify(args) },
      { KNOWWORK_TOOLS_BASE_URL: stub.baseUrl },
    )
    const sessionId = `am-${toolName}`
    try {
      await host.call('POST', '/v1/sessions', { sessionId, tools: { token: TOKEN } })
      await host.call('POST', `/v1/sessions/${sessionId}/messages`, { text: '报销标准', answerMode: 'quick' })
      await collectEvents(host, sessionId, isTurnEnd)
      assert.equal(stub.executions().length, 1, `${label}没有回调业务服务`)
      assert.ok(!modelInput(host, 1).includes(REJECTED), `${label}被拦下了`)
    } finally {
      await host.stop()
      await stub.close()
    }
  })
}

test('同一会话里的业务工具：快速问答只露出声明了快速问答的，切回智能体全部回来', async () => {
  // 放行看工具自己的声明，不看名字：不是知识库工具的也能声明（例如记忆写入），知识库工具没声明也不给
  const tools = [quickTool('knowledge_search'), quickTool('memory_note'), bizTool('knowledge_documents'), bizTool('crm_lookup')]
  const stub = await startToolsStub({ toolsByToken: { [TOKEN]: tools } })
  const host = await startHost({ sequence: ['success'], successText: '好' }, { KNOWWORK_TOOLS_BASE_URL: stub.baseUrl })
  try {
    await host.call('POST', '/v1/sessions', { sessionId: 'am-biz', tools: { token: TOKEN } })
    await host.call('POST', '/v1/sessions/am-biz/messages', { text: '报销标准', answerMode: 'quick' })
    await collectEvents(host, 'am-biz', isTurnEnd)
    assert.deepEqual(toolNamesOf(host.llm.requests[0]), ['knowledge_search', 'memory_note'])
    await host.call('POST', '/v1/sessions/am-biz/messages', { text: '查一下客户' })
    await collectEvents(host, 'am-biz', turnEnds(2))
    const back = toolNamesOf(host.llm.requests[1])
    assert.ok(['crm_lookup', 'knowledge_documents', 'knowledge_search', 'kw_echo', 'memory_note'].every(name => back.includes(name)), back.join(','))
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
