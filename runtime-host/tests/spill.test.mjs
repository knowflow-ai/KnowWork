// 大输出溢出（官方 dsh-spill-policy + 服务端的存储后端 knowwork-remote-spill）：超出预算的工具结果，模型只看到开头、结尾和
// 官方的一句「完整结果在某个路径」，路径是会话工作区里的 /workspace/tmp/tool-results/...，完整内容真的写进了会话容器。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { startHost, collectEvents, isTurnEnd } from './support/harness.mjs'
import { startOpenAiStub } from './support/openai-stub.mjs'
import { startSandboxStub } from './support/sandbox-stub.mjs'
import { bizTool, startToolsStub } from './support/tools-stub.mjs'
import { safeName } from '../plugins/dsh-host/src/remote-spill.mjs'

const TOKEN = 'spill-token-'.padEnd(40, 's')

/** 等这一轮结束：大的帧可能让事件流断开（写缓冲满了宿主就断开这个订阅），照业务服务的做法从游标接着读 */
async function waitTurnEnd(host, sessionId) {
  let after = 0
  for (let attempt = 0; attempt < 20; attempt += 1) {
    const frames = await collectEvents(host, sessionId, isTurnEnd, { after })
    if (frames.some(isTurnEnd)) return
    after = frames.positions
  }
  throw new Error('这一轮没有结束')
}
// 约 28 万字、70 多万字节：明显超过预算（官方按词元估算，中文一个字约一个词元起），又在沙箱单个文件 1MB 的写入上限之内
const BIG = Array.from({ length: 12000 }, (_, i) => `第 ${i + 1} 行：合同条款与付款节点的明细说明`).join('\n')

test('业务工具结果超出预算：模型只看到开头结尾和官方的溢出说明，完整内容写进会话工作区', async () => {
  const gateway = await startOpenAiStub({ script: [{ tool: 'kw_big', args: { q: '全部' } }, { text: '看完了' }] })
  const tools = await startToolsStub({
    toolsByToken: { [TOKEN]: [bizTool('kw_big')] },
    execute: async () => ({ body: { actionId: 'act-big', status: 'succeeded', content: BIG, truncated: false, replayed: false } }),
  })
  const sandbox = await startSandboxStub({ sessionsByToken: { [TOKEN]: 'sp-1' } })
  const host = await startHost({ sequence: ['success'] }, {
    KNOWWORK_MODEL_PROVIDER: 'knowwork-gateway', KNOWWORK_MODEL: 'kw-chat', KNOWWORK_MODEL_BASE_URL: gateway.baseURL,
    KNOWWORK_MODEL_TOKEN: 'gw-token-spill-0001-aaaaaaaa', KNOWWORK_TOOLS_BASE_URL: tools.baseUrl,
    KNOWWORK_SANDBOX_BASE_URL: sandbox.baseUrl,
  })
  try {
    assert.equal((await host.call('POST', '/v1/sessions', { sessionId: 'sp-1', tools: { token: TOKEN } })).status, 201)
    await host.call('POST', '/v1/sessions/sp-1/messages', { text: '把全部条款查出来' })
    await waitTurnEnd(host, 'sp-1')

    const chats = gateway.requests.filter(r => r.path.endsWith('/chat/completions'))
    const seen = JSON.stringify(chats.at(-1).body.messages)
    assert.match(seen, /Full formatted result stored at: \/workspace\/tmp\/tool-results\//, '溢出说明里是模型在会话里读得到的路径')
    assert.ok(seen.includes('第 1 行') && seen.includes('第 12000 行'), '开头和结尾留给模型')
    assert.ok(!seen.includes('第 6000 行'), '中间的部分不进模型上下文')
    assert.ok(seen.length < BIG.length / 3, '给模型的内容明显变小')

    const folder = join(sandbox.workspaceOf('sp-1'), 'tmp', 'tool-results')
    const files = readdirSync(folder)
    assert.equal(files.length, 1)
    assert.equal(readFileSync(join(folder, files[0]), 'utf8'), BIG, '完整内容原样写进会话工作区')
  } finally {
    await host.stop()
    await gateway.close()
    await tools.close()
    await sandbox.close()
  }
})

test('溢出文件名：随机前缀后面跟建议名字的安全写法，不会是路径', () => {
  assert.equal(safeName('kw_big.txt'), 'kw_big.txt')
  assert.equal(safeName('../../etc/passwd'), 'etc_passwd')
  assert.equal(safeName('结果 一.txt'), 'txt')
  assert.equal(safeName(''), 'result.txt')
  assert.ok(safeName('a'.repeat(200)).length <= 80)
})
