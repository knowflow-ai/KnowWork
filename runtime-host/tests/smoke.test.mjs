import { test } from 'node:test'
import assert from 'node:assert/strict'
import { startHost, collectEvents, isTurnEnd } from './support/harness.mjs'

test('宿主启动后能建会话、发一轮消息，模型请求真正打到模拟服务', async () => {
  const host = await startHost({ sequence: ['success'], successText: '你好，我是测试回答' })
  try {
    const opened = await host.call('POST', '/v1/sessions', { sessionId: 'smoke-1' })
    assert.equal(opened.status, 201, JSON.stringify(opened.body))
    const sent = await host.call('POST', '/v1/sessions/smoke-1/messages', { text: '你好' })
    assert.equal(sent.status, 202, JSON.stringify(sent.body))
    const frames = await collectEvents(host, 'smoke-1', isTurnEnd)
    const events = frames.filter(f => f.kind === 'session/event').map(f => f.data)
    assert.ok(host.llm.requests.length >= 1, '模拟模型没有收到请求')
    const turnEnd = events.find(e => e.type === 'turn/end')
    assert.ok(turnEnd, events.map(e => e.type).join(','))
    assert.ok(JSON.stringify(events).includes('你好，我是测试回答'), `回答没有出现在事件里：${events.map(e => e.type).join(',')}`)
  } finally {
    await host.stop()
  }
})

test('系统提示用中文的企业助手人设，要求调工具前先说明（官方默认是英文编程助手）', async () => {
  const host = await startHost({ sequence: ['success'], successText: '好' })
  try {
    await host.call('POST', '/v1/sessions', { sessionId: 'persona-1' })
    await host.call('POST', '/v1/sessions/persona-1/messages', { text: '你好' })
    await collectEvents(host, 'persona-1', isTurnEnd)
    const system = JSON.stringify(host.llm.requests[0]?.body?.system ?? '')
    assert.ok(system.includes('KnowWork 企业智能助手'), `系统提示里没有人设：${system.slice(0, 300)}`)
    assert.ok(system.includes('调用工具前，先用一两句话告诉用户你要做什么'), '人设里没有工具前说明的要求')
    assert.ok(!system.includes('software engineer assistant'), '官方英文人设没有被替换')
    assert.ok(system.includes('```mermaid'), '系统提示里没有说对话界面能显示 mermaid 图')
  } finally {
    await host.stop()
  }
})

test('换了人设（KNOWWORK_PERSONA）也还告诉模型对话界面能显示 mermaid 图', async () => {
  const host = await startHost({ sequence: ['success'], successText: '好' }, { KNOWWORK_PERSONA: '你是测试助手。' })
  try {
    await host.call('POST', '/v1/sessions', { sessionId: 'persona-2' })
    await host.call('POST', '/v1/sessions/persona-2/messages', { text: '你好' })
    await collectEvents(host, 'persona-2', isTurnEnd)
    const system = JSON.stringify(host.llm.requests[0]?.body?.system ?? '')
    assert.ok(system.includes('你是测试助手。') && system.includes('```mermaid'), system.slice(0, 300))
  } finally {
    await host.stop()
  }
})

test('发给模型的消息里没有工作目录上下文（无用、泄露服务器路径，还让模型改用英文思考）', async () => {
  const host = await startHost({ sequence: ['success'], successText: '好' })
  try {
    await host.call('POST', '/v1/sessions', { sessionId: 'ctx-1' })
    await host.call('POST', '/v1/sessions/ctx-1/messages', { text: '短剧有哪些代表作品？' })
    await collectEvents(host, 'ctx-1', isTurnEnd)
    // 只看模型读得到的部分
    const body = host.llm.requests[0]?.body ?? {}
    const request = JSON.stringify({ system: body.system, messages: body.messages })
    assert.ok(request.includes('短剧有哪些代表作品？'), '提问应当在请求里')
    assert.ok(!request.includes('Current working directory'), '工作目录上下文还在')
    assert.ok(!request.includes(process.cwd()), '请求里出现了宿主机路径')
  } finally {
    await host.stop()
  }
})

/** 模型读得到的部分：系统提示与消息。 */
function modelInput(host, index = 0) {
  const body = host.llm.requests[index]?.body ?? {}
  return JSON.stringify({ system: body.system, messages: body.messages })
}

test('语言规则按业务服务下发的本轮语言写在提问之后，不写在系统提示里', async () => {
  const host = await startHost({ sequence: ['success', 'success'], successText: '好' })
  try {
    await host.call('POST', '/v1/sessions', { sessionId: 'lang-1' })
    await host.call('POST', '/v1/sessions/lang-1/messages', { text: '短剧有哪些代表作品？', language: 'zh' })
    await collectEvents(host, 'lang-1', isTurnEnd)
    const zh = modelInput(host, 0)
    assert.ok(zh.indexOf('所有给用户看的文字都用简体中文') > zh.indexOf('短剧有哪些代表作品？'), '中文规则应当在提问之后')
    assert.ok(!JSON.stringify(host.llm.requests[0].body.system).includes('简体中文'), '系统提示里不应有语言规则')

    await host.call('POST', '/v1/sessions/lang-1/messages', { text: 'List the best-known micro-dramas', language: 'en' })
    await collectEvents(host, 'lang-1', frame => isTurnEnd(frame) && host.llm.requests.length >= 2)
    const en = modelInput(host, 1)
    assert.ok(en.lastIndexOf('write all user-facing text in English') > en.indexOf('List the best-known micro-dramas'))
  } finally {
    await host.stop()
  }
})

test('没有下发语言时不附语言规则；语言值不合法时拒收', async () => {
  const host = await startHost({ sequence: ['success'], successText: '好' })
  try {
    await host.call('POST', '/v1/sessions', { sessionId: 'lang-2' })
    const bad = await host.call('POST', '/v1/sessions/lang-2/messages', { text: '你好', language: 'fr' })
    assert.equal(bad.status, 400)
    await host.call('POST', '/v1/sessions/lang-2/messages', { text: '你好' })
    await collectEvents(host, 'lang-2', isTurnEnd)
    const input = modelInput(host)
    assert.ok(!input.includes('简体中文') && !input.includes('user-facing text'), '未下发语言时不应附规则')
  } finally {
    await host.stop()
  }
})

test('附件：每个文件在提问之后一行说明（逐字照官方 fileHandleText），会话历史里留着；路径不在 /workspace/input 拒收', async () => {
  const host = await startHost({ sequence: ['success', 'success', 'success', 'success'], successText: '好' })
  try {
    await host.call('POST', '/v1/sessions', { sessionId: 'att-1' })
    const file = { name: '合同 "终版".docx', path: '/workspace/input/合同 "终版".docx', bytes: 2048, sha256: 'a'.repeat(64) }
    const bad = await host.call('POST', '/v1/sessions/att-1/messages', { text: '看看', attachments: [{ ...file, path: '/etc/passwd' }] })
    assert.equal(bad.status, 400)
    await host.call('POST', '/v1/sessions/att-1/messages', {
      text: '帮我改一下付款条款',
      attachments: [{ ...file, extractedPath: '/workspace/input/合同 "终版".md' }],
    })
    await collectEvents(host, 'att-1', isTurnEnd)
    const first = modelInput(host, 0)
    // modelInput 把请求整体 JSON 序列化了一次，期望文字按同样的方式转义后再比
    const inJson = (text) => JSON.stringify(text).slice(1, -1)
    const handle = inJson('[File "合同 \\"终版\\".docx" (2048 bytes, sha256:aaaaaaaa): verbatim read-only copy saved at "/workspace/input/合同 \\"终版\\".docx". Read that path with your file tools when its contents are needed')
    assert.ok(first.indexOf(handle) > first.indexOf('帮我改一下付款条款'), '说明行跟在提问之后')
    assert.ok(first.includes(inJson('[Markdown extracted from "合同 \\"终版\\".docx" by the document parser')))

    await host.call('POST', '/v1/sessions/att-1/messages', { text: '再看一遍第三条' })
    await collectEvents(host, 'att-1', frame => isTurnEnd(frame) && host.llm.requests.length >= 2)
    assert.ok(modelInput(host, 1).includes(handle), '下一轮模型仍看得到这个文件')

    // 解析还没完成（用户点了「直接发送」、接口调用）：告诉模型解析结果会放在哪、现在还没有
    const notAPath = await host.call('POST', '/v1/sessions/att-1/messages', {
      text: '看看', attachments: [{ ...file, extractionPending: true }],
    })
    assert.equal(notAPath.status, 400, '没有 Markdown 路径时不能只说「还在解析」')
    await host.call('POST', '/v1/sessions/att-1/messages', {
      text: '总结一下',
      attachments: [{ ...file, extractedPath: '/workspace/input/合同 "终版".md', extractionPending: true }],
    })
    await collectEvents(host, 'att-1', frame => isTurnEnd(frame) && host.llm.requests.length >= 3)
    const third = modelInput(host, 2)
    assert.ok(third.includes(inJson('[Markdown extracted from "合同 \\"终版\\".docx" by the document parser is not ready yet: parsing is still running and the result will be saved at "/workspace/input/合同 \\"终版\\".md"')))

    // 给模型读的文字：放得下的整篇跟在后面，放不下的给字数、页数和目录；格式不对的目录整条拒收
    const small = { name: '说明.txt', path: '/workspace/input/说明.txt', bytes: 6, sha256: 'b'.repeat(64), text: '付款 30 天', textChars: 6 }
    const scan = { name: '扫描.pdf', path: '/workspace/input/扫描.pdf', bytes: 9, sha256: 'c'.repeat(64), extractedPath: '/workspace/input/扫描.md', text: '# 承诺书', textChars: 6, textPages: 2 }
    const big = {
      ...file, extractedPath: '/workspace/input/合同 "终版".md', textChars: 90000, textPages: 30,
      outline: [{ line: 1, text: '第 1 页：封面' }, { line: 88, text: '## 第二章 付款' }],
      digestPath: '/workspace/input/合同 "终版".导读.md',
    }
    const badOutline = await host.call('POST', '/v1/sessions/att-1/messages', {
      text: '看看', attachments: [{ ...big, outline: [{ line: 0, text: 'x' }] }],
    })
    assert.equal(badOutline.status, 400)
    await host.call('POST', '/v1/sessions/att-1/messages', { text: '对比一下', attachments: [small, scan, big] })
    await collectEvents(host, 'att-1', frame => isTurnEnd(frame) && host.llm.requests.length >= 4)
    const fourth = modelInput(host, 3)
    assert.ok(fourth.includes(inJson('[Contents of "说明.txt" (6 characters, the text of "/workspace/input/说明.txt"), included in full so this turn needs no file read.')))
    assert.ok(fourth.includes(inJson('付款 30 天')))
    // 整篇给的写明共几页、这就是全部文字，模型不用怀疑没读全、再去转原件
    assert.ok(fourth.includes(inJson('[Contents of "扫描.pdf" (6 characters across 2 pages, the text of "/workspace/input/扫描.md"), included in full so this turn needs no file read. This is all the text in the file, all 2 pages; there is no need to extract the original again.')))
    assert.ok(fourth.includes(inJson('[Contents of "合同 \\"终版\\".docx": too long to include (90000 characters across 30 pages).')))
    assert.ok(fourth.includes(inJson('L1: 第 1 页：封面\nL88: ## 第二章 付款')))
    assert.ok(fourth.includes(inJson('A digest of it is at "/workspace/input/合同 \\"终版\\".导读.md": an overall summary, then each section\'s key points with line ranges.')))
  } finally {
    await host.stop()
  }
})
