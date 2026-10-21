/**
 * 远程执行世界：官方 bash、文件工具、present 经宿主的远程 ctx.subprocess / ctx.fs 落到业务服务的沙箱接口，
 * 宿主机上不跑命令、不碰文件。会话工作目录是 /sessions/<会话 id>，路径与令牌都按会话认。
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { startHost, collectEvents, isTurnEnd } from './support/harness.mjs'
import { startOpenAiStub } from './support/openai-stub.mjs'
import { startToolsStub } from './support/tools-stub.mjs'
import { startSandboxStub } from './support/sandbox-stub.mjs'

const TOKEN = 'remote-exec-token-'.padEnd(40, 'r')

async function startRemoteHost(script) {
  const gateway = await startOpenAiStub({ script })
  const tools = await startToolsStub({ toolsByToken: { [TOKEN]: [] } })
  const sandbox = await startSandboxStub({ sessionsByToken: { [TOKEN]: 're-1' } })
  const host = await startHost({ sequence: ['success'] }, {
    KNOWWORK_MODEL_PROVIDER: 'knowwork-gateway',
    KNOWWORK_MODEL: 'kw-chat',
    KNOWWORK_MODEL_BASE_URL: gateway.baseURL,
    KNOWWORK_MODEL_TOKEN: 'gw-token-remote-0001-aaaaaaa',
    KNOWWORK_TOOLS_BASE_URL: tools.baseUrl,
    KNOWWORK_SANDBOX_BASE_URL: sandbox.baseUrl,
  })
  const chat = () => gateway.requests.filter(r => r.path.endsWith('/chat/completions'))
  return { host, sandbox, chat, stop: async () => { await host.stop(); await gateway.close(); await tools.close(); await sandbox.close() } }
}

test('官方 bash 写文件、read 读回、present 交付，全部经沙箱接口落到会话工作目录', async () => {
  const env = await startRemoteHost([
    { tool: 'bash', args: { command: 'echo "第一行" > report.txt && pwd', description: '写报告' } },
    { tool: 'read', args: { file_path: 'report.txt' } },
    { tool: 'present', args: { files: [{ path: 'report.txt', description: '报告' }] } },
    { text: '完成' },
  ])
  try {
    assert.equal((await env.host.call('POST', '/v1/sessions', { sessionId: 're-1', tools: { token: TOKEN } })).status, 201)
    await env.host.call('POST', '/v1/sessions/re-1/messages', { text: '写个报告' })
    await collectEvents(env.host, 're-1', isTurnEnd)

    const exec = env.sandbox.requests.find(r => r.operation === 'exec')
    assert.ok(exec, 'bash 没有走沙箱接口')
    assert.deepEqual(exec.body.argv.slice(0, 2), ['bash', '-c'])
    assert.equal(exec.body.cwd, '/sessions/re-1')
    assert.equal(exec.body.env.DSH_HOME, undefined, '宿主机路径被带进了容器')
    assert.ok(existsSync(join(env.sandbox.workspaceOf('re-1'), 'report.txt')), '文件没写进会话工作目录')
    assert.equal(readFileSync(join(env.sandbox.workspaceOf('re-1'), 'report.txt'), 'utf8'), '第一行\n')

    const results = env.chat().at(-1).body.messages.filter(m => m.role === 'tool').map(m => String(m.content))
    assert.ok(results.some(text => text.includes('第一行')), `read 没读回内容：${results.join(' | ')}`)
    assert.ok(results.some(text => text.includes('Presented /sessions/re-1/report.txt')), `present 没交付：${results.join(' | ')}`)
    assert.ok(env.sandbox.requests.some(r => r.operation === 'read'), 'read 没走沙箱接口')
  } finally {
    await env.stop()
  }
})

test('/workspace 开头的路径按会话工作目录解析（容器里是同一个目录）', async () => {
  const env = await startRemoteHost([
    { tool: 'write', args: { file_path: '/workspace/output/a.txt', content: '你好' } },
    { tool: 'present', args: { files: [{ path: '/workspace/output/a.txt' }] } },
    { text: '好' },
  ])
  try {
    await env.host.call('POST', '/v1/sessions', { sessionId: 're-1', tools: { token: TOKEN } })
    await env.host.call('POST', '/v1/sessions/re-1/messages', { text: '写个文件' })
    await collectEvents(env.host, 're-1', isTurnEnd)
    assert.equal(readFileSync(join(env.sandbox.workspaceOf('re-1'), 'output/a.txt'), 'utf8'), '你好')
    const results = env.chat().at(-1).body.messages.filter(m => m.role === 'tool').map(m => String(m.content))
    assert.ok(results.some(text => text.includes('Presented /sessions/re-1/output/a.txt')), results.join(' | '))
  } finally {
    await env.stop()
  }
})

test('会话工作目录之外的路径一律拒绝，不回落到宿主机文件', async () => {
  const env = await startRemoteHost([
    { tool: 'read', args: { file_path: '/etc/passwd' } },
    { text: '好' },
  ])
  try {
    await env.host.call('POST', '/v1/sessions', { sessionId: 're-1', tools: { token: TOKEN } })
    await env.host.call('POST', '/v1/sessions/re-1/messages', { text: '读系统文件' })
    await collectEvents(env.host, 're-1', isTurnEnd)
    const results = env.chat().at(-1).body.messages.filter(m => m.role === 'tool').map(m => String(m.content))
    assert.ok(results.some(text => text.includes('only files under the session working directory')), results.join(' | '))
    assert.ok(!results.some(text => text.includes('root:')), '读到了宿主机文件')
  } finally {
    await env.stop()
  }
})
