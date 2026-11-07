/**
 * 远程执行世界：官方 bash、文件工具、present 经宿主的远程 ctx.subprocess / ctx.fs 落到业务服务的沙箱接口，
 * 宿主机上不跑命令、不碰文件。会话工作目录是 /sessions/<会话 id>，路径与令牌都按会话认。
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { startHost, collectEvents, isTurnEnd } from './support/harness.mjs'
import { startOpenAiStub } from './support/openai-stub.mjs'
import { startToolsStub } from './support/tools-stub.mjs'
import { startSandboxStub } from './support/sandbox-stub.mjs'
import { publishedSkill, startSkillsStub } from './support/skills-stub.mjs'

const TOKEN = 'remote-exec-token-'.padEnd(40, 'r')

// 生产配置不开 apply_patch，它的测试叠加这份配置打开
const APPLY_PATCH = join(import.meta.dirname, 'overlays', 'apply-patch.yml')

async function startRemoteHost(script, options = {}) {
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
  }, options)
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

test('加载过技能以后，执行命令时报上这个会话加载过的技能（业务服务据此加上它的环境变量，宿主不经手值）', async () => {
  const skillsToken = 'remote-skill-token-'.padEnd(40, 's')
  const report = publishedSkill('report-sync', { body: '运行 sync.sh，它读 REPORT_API_KEY' })
  const skills = await startSkillsStub({ skillsByToken: { [skillsToken]: [report] } })
  const gateway = await startOpenAiStub({ script: [
    { tool: 'bash', args: { command: 'echo before', description: '加载前' } },
    { tool: 'skill', args: { name: 'report-sync' } },
    { tool: 'bash', args: { command: 'echo after', description: '加载后' } },
    { text: '完成' },
  ] })
  const tools = await startToolsStub({ toolsByToken: { [TOKEN]: [] } })
  const sandbox = await startSandboxStub({ sessionsByToken: { [TOKEN]: 're-env' } })
  const host = await startHost({ sequence: ['success'] }, {
    KNOWWORK_MODEL_PROVIDER: 'knowwork-gateway',
    KNOWWORK_MODEL: 'kw-chat',
    KNOWWORK_MODEL_BASE_URL: gateway.baseURL,
    KNOWWORK_MODEL_TOKEN: 'gw-token-remote-0002-aaaaaaa',
    KNOWWORK_TOOLS_BASE_URL: tools.baseUrl,
    KNOWWORK_SANDBOX_BASE_URL: sandbox.baseUrl,
    KNOWWORK_SKILLS_BASE_URL: skills.baseUrl,
  })
  try {
    await host.call('POST', '/v1/sessions', { sessionId: 're-env', tools: { token: TOKEN }, skills: { token: skillsToken } })
    await host.call('POST', '/v1/sessions/re-env/messages', { text: '同步报表' })
    await collectEvents(host, 're-env', isTurnEnd)
    const execs = sandbox.requests.filter(r => r.operation === 'exec')
    assert.equal(execs.length, 2)
    assert.equal(execs[0].body.skills, undefined, '还没加载技能就报了技能')
    assert.deepEqual(execs[1].body.skills, ['report-sync'])
    assert.ok(!JSON.stringify(execs[1].body).includes('REPORT_API_KEY='), '宿主不该经手环境变量的值')
  } finally {
    await host.stop(); await gateway.close(); await tools.close(); await sandbox.close(); await skills.close()
  }
})

test('官方 apply_patch（生产不开，这里叠加配置打开）一次改几个文件：改、加、删、移都落到会话容器；没读过的文件照样被「先读再改」挡住', async () => {
  const patch = [
    '*** Begin Patch',
    '*** Update File: a.txt', '@@', '-旧的一行', '+新的一行',
    '*** Add File: out/new.txt', '+新文件',
    '*** Delete File: old.txt',
    '*** Update File: keep.txt', '*** Move to: moved/keep.txt', '@@', ' 保留', '+补一行',
    '*** End Patch',
  ].join('\n')
  const env = await startRemoteHost([
    { tool: 'bash', args: { command: 'printf "旧的一行\n" > a.txt && printf "删\n" > old.txt && printf "保留\n" > keep.txt', description: '准备' } },
    { tool: 'apply_patch', args: { input: patch } },
    { tool: 'read', args: { file_path: 'a.txt' } },
    { tool: 'read', args: { file_path: 'old.txt' } },
    { tool: 'read', args: { file_path: 'keep.txt' } },
    { tool: 'apply_patch', args: { input: patch } },
    { text: '改好了' },
  ], { patches: [APPLY_PATCH] })
  try {
    await env.host.call('POST', '/v1/sessions', { sessionId: 're-1', tools: { token: TOKEN } })
    await env.host.call('POST', '/v1/sessions/re-1/messages', { text: '一起改' })
    await collectEvents(env.host, 're-1', isTurnEnd)
    const root = env.sandbox.workspaceOf('re-1')
    const results = env.chat().at(-1).body.messages.filter(m => m.role === 'tool').map(m => String(m.content))
    assert.match(results[1], /read|first|FS_NOT_OBSERVED/i, `没读过就改应被挡住：${results[1]}`)
    assert.equal(readFileSync(join(root, 'a.txt'), 'utf8'), '新的一行\n')
    assert.equal(readFileSync(join(root, 'out/new.txt'), 'utf8'), '新文件\n')
    assert.ok(!existsSync(join(root, 'old.txt')), '删除没落到容器')
    assert.ok(!existsSync(join(root, 'keep.txt')), '移动后源文件还在')
    assert.equal(readFileSync(join(root, 'moved/keep.txt'), 'utf8'), '保留\n补一行\n')
    assert.ok(env.sandbox.requests.some(r => r.operation === 'delete'), '删除没走沙箱接口')
  } finally {
    await env.stop()
  }
})

test('读文件、编辑文件的来回次数：读是官方 stat 加一次 read，编辑是一次 read 加一次 write（版本取自读回来的 stat）', async () => {
  const env = await startRemoteHost([
    { tool: 'bash', args: { command: 'echo 一行 > note.txt', description: '写' } },
    { tool: 'read', args: { file_path: 'note.txt' } },
    { tool: 'edit', args: { file_path: 'note.txt', old_string: '一行', new_string: '两行' } },
    { text: '完成' },
  ])
  try {
    await env.host.call('POST', '/v1/sessions', { sessionId: 're-1', tools: { token: TOKEN } })
    await env.host.call('POST', '/v1/sessions/re-1/messages', { text: '读了再改' })
    await collectEvents(env.host, 're-1', isTurnEnd)
    const ops = env.sandbox.requests.filter(r => r.body.path?.endsWith('note.txt')).map(r => r.operation)
    assert.deepEqual(ops, ['stat', 'read', 'read', 'write'], '每次都是宿主 → 业务服务 → docker exec 一个来回，不要多出重复的 stat')
    assert.equal(readFileSync(join(env.sandbox.workspaceOf('re-1'), 'note.txt'), 'utf8'), '两行\n')
    const offered = (env.chat()[0].body.tools ?? []).map(t => t.function?.name ?? t.name)
    assert.ok(offered.includes('edit') && !offered.includes('apply_patch'), '生产配置改文件用 edit、write，不给 apply_patch')
  } finally {
    await env.stop()
  }
})

test('一个会话的模型落不到别人的会话：即使知道对方的会话 id，读文件、在对方工作目录执行命令都被拒，自己的照常', async () => {
  const OTHER = 'remote-exec-other-'.padEnd(40, 'o')
  const gateway = await startOpenAiStub({ script: [
    { tool: 'read', args: { file_path: '/sessions/re-2/secret.txt' } },
    { tool: 'bash', args: { command: 'cat secret.txt', description: '读别人的', workdir: '/sessions/re-2' } },
    { tool: 'bash', args: { command: 'echo 自己的', description: '自己的' } },
    { text: '完成' },
  ] })
  const tools = await startToolsStub({ toolsByToken: { [TOKEN]: [], [OTHER]: [] } })
  const sandbox = await startSandboxStub({ sessionsByToken: { [TOKEN]: 're-1', [OTHER]: 're-2' } })
  const host = await startHost({ sequence: ['success'] }, {
    KNOWWORK_MODEL_PROVIDER: 'knowwork-gateway',
    KNOWWORK_MODEL: 'kw-chat',
    KNOWWORK_MODEL_BASE_URL: gateway.baseURL,
    KNOWWORK_MODEL_TOKEN: 'gw-token-isolate-01-aaaaaaaa',
    KNOWWORK_TOOLS_BASE_URL: tools.baseUrl,
    KNOWWORK_SANDBOX_BASE_URL: sandbox.baseUrl,
  })
  try {
    assert.equal((await host.call('POST', '/v1/sessions', { sessionId: 're-1', tools: { token: TOKEN } })).status, 201)
    assert.equal((await host.call('POST', '/v1/sessions', { sessionId: 're-2', tools: { token: OTHER } })).status, 201)
    mkdirSync(sandbox.workspaceOf('re-2'), { recursive: true })
    writeFileSync(join(sandbox.workspaceOf('re-2'), 'secret.txt'), '别人的机密')
    await host.call('POST', '/v1/sessions/re-1/messages', { text: '去看看别人的文件' })
    await collectEvents(host, 're-1', isTurnEnd)
    assert.deepEqual(sandbox.requests.filter(r => r.token === OTHER), [], '用别人会话的令牌发了沙箱请求')
    assert.ok(sandbox.requests.some(r => r.token === TOKEN && r.operation === 'exec'), '自己的命令没有照常执行')
    const seen = JSON.stringify(gateway.requests.map(r => r.body?.messages ?? []))
    assert.ok(!seen.includes('别人的机密'), '别人的文件内容到了模型手里')
  } finally {
    await host.stop()
    await gateway.close()
    await tools.close()
    await sandbox.close()
  }
})
