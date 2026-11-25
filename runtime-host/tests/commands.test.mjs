/**
 * 用户的 / 命令（commands.mjs，官方 dsh-commands）：真实 DSH 组合 + 业务网关桩 + 业务工具桩。
 * - 开了「长任务目标」的会话列得出 /goal，别的会话没有；
 * - 执行直接作用在 Agent 上、不发给模型：/goal 设好目标，再敲 /goal 看得到；命令那一行不进模型的历史；
 * - 会话日志里记了 command/run、command/done（业务服务据此画命令气泡）；
 * - 不认识的命令、格式不对的请求报错，不当提问。
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { startHost, collectEvents, isTurnEnd } from './support/harness.mjs'
import { startOpenAiStub } from './support/openai-stub.mjs'
import { bizTool, startToolsStub } from './support/tools-stub.mjs'

const OVERLAY = join(dirname(fileURLToPath(import.meta.url)), 'overlays', 'plugin-presets.yml')
const TOKEN = 'command-tools-token-'.padEnd(40, 'c')
const chat = gateway => gateway.requests.filter(r => r.path.endsWith('/chat/completions'))

test('开了插件的会话列得出它的命令；执行不发给模型、记进会话日志', async () => {
  const gateway = await startOpenAiStub({ script: [{ text: '好的，开始核对' }] })
  const tools = await startToolsStub({ toolsByToken: { [TOKEN]: [bizTool('kw_biz_lookup')] } })
  const host = await startHost({ sequence: ['success'] }, {
    KNOWWORK_MODEL_PROVIDER: 'knowwork-gateway', KNOWWORK_MODEL: 'kw-chat',
    KNOWWORK_MODEL_BASE_URL: gateway.baseURL, KNOWWORK_MODEL_TOKEN: 'gw-token-command-0001-aaaaaaaa',
    KNOWWORK_TOOLS_BASE_URL: tools.baseUrl,
  }, { patches: [OVERLAY] })
  try {
    for (const [id, plugins] of [['c-goal', ['goal']], ['c-plain', undefined]]) {
      const opened = await host.call('POST', '/v1/sessions', { sessionId: id, tools: { token: TOKEN }, ...plugins ? { plugins } : {} })
      assert.ok(opened.status < 300, JSON.stringify(opened.body))
    }
    const listed = await host.call('GET', '/v1/sessions/c-goal/commands')
    assert.equal(listed.status, 200)
    const goal = listed.body.commands.find(command => command.name === 'goal')
    assert.ok(goal, JSON.stringify(listed.body))
    assert.equal(goal.definitionId, '@deepseek-ai/dsh-command-goal')
    assert.ok(goal.hint.includes('objective'))
    const plain = await host.call('GET', '/v1/sessions/c-plain/commands')
    assert.ok(!plain.body.commands.some(command => command.name === 'goal'), '没开插件的会话没有 /goal')

    const set = await host.call('POST', '/v1/sessions/c-goal/commands', { line: '/goal 把三季度的合同都核对一遍' })
    assert.equal(set.status, 200, JSON.stringify(set.body))
    assert.equal(set.body.kind, 'success')
    assert.match(set.body.commandId, /^cmd-/)
    assert.equal(chat(gateway).length, 0, '命令不发给模型')
    const shown = await host.call('POST', '/v1/sessions/c-goal/commands', { line: '/goal' })
    assert.match(shown.body.text, /三季度的合同/, '不带参数是看当前目标')

    const frames = await collectEvents(host, 'c-goal', (_frame, all) =>
      all.filter(f => f.kind === 'session/event' && f.data?.type === 'command/done').length >= 2)
    const logged = frames.filter(f => f.kind === 'session/event' && f.data?.type?.startsWith('command/')).map(f => f.data)
    assert.deepEqual(logged.map(e => e.type), ['command/run', 'command/done', 'command/run', 'command/done'])
    assert.equal(logged[0].data.name, 'goal')
    assert.equal(logged[0].data.args.trim(), '把三季度的合同都核对一遍')
    assert.equal(logged[1].data.commandId, set.body.commandId)

    const sent = await host.call('POST', '/v1/sessions/c-goal/messages', { text: '开始吧' })
    assert.equal(sent.status, 202)
    await collectEvents(host, 'c-goal', isTurnEnd, { timeoutMs: 60_000 })
    // 用命令设的目标不进提示词：官方靠自动续跑（dsh-goal-round-driver）每轮把目标交给模型，我们没挂它，模型要自己调 get_goal
    assert.ok(!JSON.stringify(chat(gateway)[0].body.messages).includes('/goal'), '命令那一行不进模型的历史')
    assert.ok(chat(gateway)[0].body.tools.some(tool => tool.function?.name === 'get_goal'))

    const unknown = await host.call('POST', '/v1/sessions/c-plain/commands', { line: '/goal 不该成功' })
    assert.equal(unknown.status, 404)
    assert.equal(unknown.body.error.code, 'command_unknown')
    const bad = await host.call('POST', '/v1/sessions/c-goal/commands', { line: '你好' })
    assert.equal(bad.status, 400)
    const gone = await host.call('GET', '/v1/sessions/c-missing/commands')
    assert.equal(gone.status, 404)
  } finally {
    await host.stop()
    await gateway.close()
    await tools.close()
  }
})
