/**
 * 工作流试点：官方 workflow 工具（dsh-tool-workflow + dsh-workflow-ptc）在我们的宿主里能不能用。
 * 父 Agent 提交一段编排脚本，脚本在会话容器里跑（PTC 经流式执行），用 parallel + agent() 并发派三个子 Agent，
 * 每个子 Agent 用官方 bash（收集模式，经业务服务执行接口）数一个文件的行数，脚本把三个结果汇总返回。
 * 模型用按内容编排的桩（子 Agent 并发，到达顺序不定）。前提同 ssh-world.test.mjs，缺就跳过。
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { startHost, collectEvents, isTurnEnd } from './support/harness.mjs'
import { startOpenAiStub } from './support/openai-stub.mjs'
import { startToolsStub } from './support/tools-stub.mjs'
import { startSandboxStub } from './support/sandbox-stub.mjs'
import { missingSshPrerequisites, startSshContainer } from './support/ssh-container.mjs'

const TOKEN = 'workflow-pilot-token-'.padEnd(40, 'w')
const SESSION = 'wf-1'
const OVERLAY = join(import.meta.dirname, 'overlays', 'workflow.yml')
const FILES = { 'a.txt': 'x\ny\nz\n', 'b.txt': 'only\n', 'c.txt': 'one\ntwo\n' }
const ASK = /数一下 (\w+\.txt) 的行数/

const SCRIPT = [
  'const counts = await parallel(args.files.map(file => () => agent(`数一下 ${file} 的行数，只回数字`)))',
  'return { files: args.files, counts: counts.map(Number) }',
].join('\n')

const textOf = message => typeof message.content === 'string' ? message.content
  : (message.content ?? []).map(part => part.text ?? '').join('')

/** 父 Agent：先调 workflow，拿到结果后收尾；子 Agent：先用 bash 数行，再只回数字。 */
function pick(body) {
  const messages = body.messages ?? []
  const asked = messages.map(textOf).map(text => ASK.exec(text)).find(Boolean)
  const toolResults = messages.filter(m => m.role === 'tool')
  if (asked !== undefined) {
    if (toolResults.length === 0) return { tool: 'bash', args: { command: `wc -l < ${asked[1]}`, description: '数行数' } }
    return { text: (textOf(toolResults.at(-1)).match(/\d+/) ?? ['?'])[0] }
  }
  if (toolResults.length === 0) {
    return { tool: 'workflow', args: { meta: { name: 'count-lines', description: '并发数三个文件的行数' }, script: SCRIPT,
      args: { files: Object.keys(FILES) } } }
  }
  return { text: '汇总完成' }
}

const skip = missingSshPrerequisites()

test('工作流试点：脚本在会话容器里并发派三个子 Agent，各自数一个文件的行数，汇总返回', { skip }, async () => {
  const container = await startSshContainer(SESSION)
  const gateway = await startOpenAiStub({ pick })
  const tools = await startToolsStub({ toolsByToken: { [TOKEN]: [] } })
  const sandbox = await startSandboxStub({ sessionsByToken: { [TOKEN]: SESSION }, ssh: { containers: { [SESSION]: container.name } } })
  for (const [file, text] of Object.entries(FILES)) writeFileSync(join(sandbox.workspaceOf(SESSION), file), text)
  const host = await startHost({ sequence: ['success'] }, {
    KNOWWORK_MODEL_PROVIDER: 'knowwork-gateway',
    KNOWWORK_MODEL: 'kw-chat',
    KNOWWORK_MODEL_BASE_URL: gateway.baseURL,
    KNOWWORK_MODEL_TOKEN: 'gw-token-workflow-0001-aaaaa',
    KNOWWORK_TOOLS_BASE_URL: tools.baseUrl,
    KNOWWORK_SANDBOX_BASE_URL: sandbox.baseUrl,
  }, { patches: [OVERLAY] })
  try {
    assert.equal((await host.call('POST', '/v1/sessions', { sessionId: SESSION, tools: { token: TOKEN } })).status, 201)
    const started = Date.now()
    await host.call('POST', `/v1/sessions/${SESSION}/messages`, { text: '统计三个文件各有几行' })
    await collectEvents(host, SESSION, isTurnEnd, { timeoutMs: 120_000 })
    console.log(`工作流一轮：${Date.now() - started} ms`)

    const chats = gateway.requests.filter(r => r.path.endsWith('/chat/completions'))
    const last = chats.at(-1)
    const result = textOf(last.body.messages.filter(m => m.role === 'tool').at(-1))
    assert.match(result, /workflow "count-lines" completed \(3 agents\)/, result)
    const value = JSON.parse(result.slice(result.indexOf('{')))
    assert.deepEqual(value, { files: ['a.txt', 'b.txt', 'c.txt'], counts: [3, 1, 2] })

    const counted = sandbox.requests.filter(r => r.operation === 'exec' && /wc -l/.test(r.body.argv.join(' ')))
    assert.equal(counted.length, 3, '三个子 Agent 各自用 bash 数了一次（收集模式，走业务服务）')
    assert.equal(sandbox.requests.filter(r => r.operation === 'ssh').length, 1, '编排脚本经流式执行在容器里跑')
    assert.ok(chats.length >= 8, `父 Agent 两次 + 每个子 Agent 两次模型调用，实际 ${chats.length}`)
  } catch (error) {
    console.log(`宿主输出：\n${host.stdout().slice(-6000)}\n${host.stderr().slice(-3000)}`)
    throw error
  } finally {
    await host.stop()
    await gateway.close()
    await tools.close()
    await sandbox.close()
    container.remove()
  }
})
