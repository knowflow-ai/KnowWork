import { test } from 'node:test'
import assert from 'node:assert/strict'
import { startHost, collectEvents, isTurnEnd } from './support/harness.mjs'
import { bizTool, startToolsStub } from './support/tools-stub.mjs'

// 知识库目录照官方技能目录：看得见 knowledge_search 才给；只列名字与简介；没变不重发；变了追加替换；清空也说一声

const TOKEN = 'tools-token-kc-'.padEnd(40, 'k')
const PLAIN = 'tools-token-kp-'.padEnd(40, 'p')
const OPEN = '<available_knowledge_bases>'

const catalog = (...bases) => ({
  bases: bases.map(([id, name, description, fields, routes]) => ({
    id, name, description, ...(fields ? { fields } : {}), ...(routes ? { routes } : {}),
  })),
  omitted: 0,
})
const count = (text, part) => text.split(part).length - 1

async function start() {
  const stub = await startToolsStub({ toolsByToken: { [TOKEN]: [bizTool('knowledge_search')], [PLAIN]: [bizTool('kw_other')] } })
  const host = await startHost({ sequence: ['success'], successText: '好' }, { KNOWWORK_TOOLS_BASE_URL: stub.baseUrl })
  return { host, stop: async () => { await host.stop(); await stub.close() } }
}

/** 发一轮，等它结束，返回这一轮模型收到的消息（JSON 文本）。 */
async function turn(host, sessionId, body) {
  const before = host.llm.requests.length
  const sent = await host.call('POST', `/v1/sessions/${sessionId}/messages`, body)
  assert.equal(sent.status, 202, JSON.stringify(sent.body))
  await collectEvents(host, sessionId, frame => isTurnEnd(frame) && host.llm.requests.length > before)
  return JSON.stringify(host.llm.requests.at(-1)?.body?.messages ?? [])
}

test('看得见 knowledge_search 的会话收到目录：只列 id、名字、简介，名字里的尖括号转义', async () => {
  const env = await start()
  try {
    await env.host.call('POST', '/v1/sessions', { sessionId: 'kc-1', tools: { token: TOKEN } })
    const first = await turn(env.host, 'kc-1', {
      text: '采购合同的付款期限是多久？',
      knowledge: catalog(['builtin.kb_a', '合同库<内部>', '采购合同与补充协议', ['部门（文本）', '签订日期（日期）'],
        ['multi_hop', 'visual']], ['kf1.ds1', 'KnowFlow 制度库', '']),
    })
    assert.equal(count(first, OPEN), 1, first.slice(0, 600))
    assert.ok(first.includes('`builtin.kb_a`：合同库&lt;内部&gt;。采购合同与补充协议'), '名字转义、简介跟在后面')
    assert.ok(first.includes('`kf1.ds1`：KnowFlow 制度库'), '没有简介只写名字')
    // 能筛的字段跟在库后面：模型不用多调一次就知道能按什么筛，可选值与标签照旧按需取
    // 能开的检索方式也跟在库后面（knowledge_search 的 multi_hop、visual），只给标了的库
    assert.ok(first.includes('采购合同与补充协议（可筛选：部门（文本）、签订日期（日期）；可开：多跳检索、视觉检索）'),
      first.slice(0, 900))
    assert.ok(first.includes('「可开」是这个库能按需打开的检索方式'), '有库标了才说明「可开」')
    assert.ok(first.includes('可选值和标签集调 knowledge_bases'), '可选值与标签照旧按需去取')
    assert.ok(!first.includes('<内部>'), '原样的尖括号不能出现')
  } finally {
    await env.stop()
  }
})

test('目录没变不重发；变了追加一份替换目录；清空时也追加一份', async () => {
  const env = await start()
  try {
    await env.host.call('POST', '/v1/sessions', { sessionId: 'kc-2', tools: { token: TOKEN } })
    const one = catalog(['builtin.kb_a', '合同库', ''])
    await turn(env.host, 'kc-2', { text: '第一问', knowledge: one })
    const same = await turn(env.host, 'kc-2', { text: '第二问', knowledge: one })
    assert.equal(count(same, OPEN), 1, '没变不该再发')
    assert.ok(!same.includes('「可开」'), '没有库能开多跳、视觉检索时不提')
    const keep = await turn(env.host, 'kc-2', { text: '第三问' })
    assert.equal(count(keep, OPEN), 1, '没下发目录的一轮沿用上一轮的')
    const changed = await turn(env.host, 'kc-2', { text: '第四问', knowledge: catalog(['builtin.kb_b', '制度库', '']) })
    assert.equal(count(changed, OPEN), 2)
    assert.ok(changed.includes('这份完整目录取代之前所有的知识库目录') && changed.includes('`builtin.kb_b`：制度库'))
    const cleared = await turn(env.host, 'kc-2', { text: '第五问', knowledge: catalog() })
    assert.ok(cleared.includes('现在没有可以检索的知识库'))
  } finally {
    await env.stop()
  }
})

test('看不见 knowledge_search 的会话不给目录；目录格式不对拒收', async () => {
  const env = await start()
  try {
    await env.host.call('POST', '/v1/sessions', { sessionId: 'kc-3', tools: { token: PLAIN } })
    const plain = await turn(env.host, 'kc-3', { text: '你好', knowledge: catalog(['builtin.kb_a', '合同库', '']) })
    assert.ok(!plain.includes(OPEN), '没有检索工具却收到了目录')
    const badFields = [catalog(['builtin.kb_a', '合同库', '', 'x']), catalog(['builtin.kb_a', '合同库', '', [1]]),
      catalog(['builtin.kb_a', '合同库', '', Array.from({ length: 31 }, (_, i) => `字段${i}（文本）`)]),
      catalog(['builtin.kb_a', '合同库', '', [], ['graph']]), catalog(['builtin.kb_a', '合同库', '', [], 'visual'])]
    for (const knowledge of [{ bases: 'x' }, catalog(['bad id!', '合同库', '']), { bases: [], omitted: -1 }, ...badFields]) {
      const bad = await env.host.call('POST', '/v1/sessions/kc-3/messages', { text: '你好', knowledge })
      assert.equal(bad.status, 400, JSON.stringify(knowledge))
    }
  } finally {
    await env.stop()
  }
})
