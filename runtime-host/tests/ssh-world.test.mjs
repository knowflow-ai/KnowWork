/**
 * 流式执行端到端：宿主的 remote-subprocess 把管道、控制管道、终端交给 DSH 官方 SSH 执行环境，经业务服务的字节管道
 * （这里由 sandbox-stub 照业务服务的做法扮演）连进真实会话容器里的 sshd -i；收集模式的 bash 照旧走 HTTP 执行接口。
 * 容器按生产姿态起（去掉全部能力、加回业务服务的那几项和 SYS_CHROOT、no-new-privileges、断网、启动时生成主机密钥）。
 *
 * 要本机有 Docker、OpenSSH 客户端与 nc，并已构建带 sshd 的沙箱镜像（见 support/ssh-container.mjs）；缺任何一样就跳过。
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { startHost, collectEvents, isTurnEnd } from './support/harness.mjs'
import { startOpenAiStub } from './support/openai-stub.mjs'
import { startToolsStub } from './support/tools-stub.mjs'
import { startSandboxStub } from './support/sandbox-stub.mjs'
import { missingSshPrerequisites, run, startSshContainer, until } from './support/ssh-container.mjs'

const TOKEN = 'ssh-world-token-'.padEnd(40, 's')
const SESSION = 'sw-1'

const skip = missingSshPrerequisites()

test('流式执行：PTC 控制管道、stdin/stdout 管道、终端经官方 SSH 执行环境落到会话容器，会话释放后环境关掉', { skip }, async () => {
  const container = await startSshContainer(SESSION)
  const CONTAINER = container.name
  const helperRunning = container.helperRunning
  const gateway = await startOpenAiStub({ script: [
    { tool: 'kw_ptc_probe', args: {} },
    { tool: 'kw_pipe_probe', args: {} },
    { tool: 'kw_terminal_probe', args: {} },
    { tool: 'bash', args: { command: 'echo collected', description: '收集模式' } },
    { text: '完成' },
    { tool: 'kw_pipe_probe', args: {} },
    { text: '断开重连后也完成' },
  ] })
  const tools = await startToolsStub({ toolsByToken: { [TOKEN]: [] } })
  const sandbox = await startSandboxStub({ sessionsByToken: { [TOKEN]: SESSION }, ssh: { containers: { [SESSION]: CONTAINER } } })
  const host = await startHost({ sequence: ['success'] }, {
    KNOWWORK_MODEL_PROVIDER: 'knowwork-gateway',
    KNOWWORK_MODEL: 'kw-chat',
    KNOWWORK_MODEL_BASE_URL: gateway.baseURL,
    KNOWWORK_MODEL_TOKEN: 'gw-token-ssh-world-0001-aaaa',
    KNOWWORK_TOOLS_BASE_URL: tools.baseUrl,
    KNOWWORK_SANDBOX_BASE_URL: sandbox.baseUrl,
  })
  try {
    assert.equal((await host.call('POST', '/v1/sessions', { sessionId: SESSION, tools: { token: TOKEN } })).status, 201)
    await host.call('POST', `/v1/sessions/${SESSION}/messages`, { text: '跑一遍流式探针' })
    await collectEvents(host, SESSION, isTurnEnd, { timeoutMs: 120_000 })

    const last = gateway.requests.filter(r => r.path.endsWith('/chat/completions')).at(-1)
    const results = last.body.messages.filter(m => m.role === 'tool').map(m => String(m.content))
    const parsed = results.slice(0, 3).map(text => JSON.parse(text.slice(text.indexOf('{'))))
    assert.deepEqual(parsed[0], { value: { n: 5, inContainer: true, cwd: '/workspace' } }, `PTC：${results[0]}`)
    assert.deepEqual(parsed[1], { seen: 'ping', exitCode: 0 }, `管道：${results[1]}`)
    assert.deepEqual(parsed[2], { kept: true }, `终端：${results[2]}`)
    assert.ok(results[3].includes('collected'), `收集模式的 bash：${results[3]}`)

    assert.equal(sandbox.requests.filter(r => r.operation === 'ssh-identity').length, 1, '一个会话只开一个执行环境')
    assert.equal(sandbox.requests.filter(r => r.operation === 'ssh').length, 1)
    assert.ok(sandbox.requests.some(r => r.operation === 'exec' && r.body.argv.join(' ').includes('echo collected')),
      '收集模式的 bash 应当照旧走 HTTP 执行接口')
    assert.ok(helperRunning(), '执行环境开着时容器里有官方辅助程序')

    // 连接断了（业务服务重启、容器被换）：杀掉容器里的辅助程序，官方连接随之失败；下一次流式执行要自动重开
    run('docker', ['exec', CONTAINER, 'pkill', '-f', 'helper.js'])
    await until(() => !helperRunning(), '辅助程序被杀掉')
    await new Promise(resolve => setTimeout(resolve, 500))
    await host.call('POST', `/v1/sessions/${SESSION}/messages`, { text: '再跑一次管道' })
    await collectEvents(host, SESSION, isTurnEnd, { after: 0, timeoutMs: 120_000 })
    await until(() => gateway.requests.filter(r => r.path.endsWith('/chat/completions')).length >= 7, '第二轮结束')
    const again = gateway.requests.filter(r => r.path.endsWith('/chat/completions')).at(-1)
      .body.messages.filter(m => m.role === 'tool').map(m => String(m.content)).at(-1)
    assert.deepEqual(JSON.parse(again.slice(again.indexOf('{'))), { seen: 'ping', exitCode: 0 }, `重连后的管道：${again}`)
    assert.equal(sandbox.requests.filter(r => r.operation === 'ssh').length, 2, '旧连接失败后重开了一个执行环境')

    assert.equal((await host.call('DELETE', `/v1/sessions/${SESSION}`)).status, 200)
    await until(() => !helperRunning(), '会话释放后辅助程序退出')
  } catch (error) {
    // 失败时把宿主日志的尾巴与桩收到的请求带出来，不然只看得到「等待超时」
    console.log(`宿主输出：\n${host.stdout().slice(-6000)}\n${host.stderr().slice(-3000)}`)
    console.log(`沙箱桩收到：${JSON.stringify(sandbox.requests.map(r => r.operation))}`)
    throw error
  } finally {
    await host.stop()
    await gateway.close()
    await tools.close()
    await sandbox.close()
    container.remove()
  }
})

test('流式执行：没有进程在跑、空闲到时，执行环境自己关掉（不靠宿主的会话释放），容器不再被占着', { skip }, async () => {
  const session = 'sw-idle'
  const token = 'ssh-world-idle-token-'.padEnd(40, 'i')
  const container = await startSshContainer(session)
  const gateway = await startOpenAiStub({ script: [{ tool: 'kw_pipe_probe', args: {} }, { text: '完成' }] })
  const tools = await startToolsStub({ toolsByToken: { [token]: [] } })
  const sandbox = await startSandboxStub({ sessionsByToken: { [token]: session }, ssh: { containers: { [session]: container.name } } })
  const host = await startHost({ sequence: ['success'] }, {
    KNOWWORK_MODEL_PROVIDER: 'knowwork-gateway',
    KNOWWORK_MODEL: 'kw-chat',
    KNOWWORK_MODEL_BASE_URL: gateway.baseURL,
    KNOWWORK_MODEL_TOKEN: 'gw-token-ssh-idle-0001-aaaaaa',
    KNOWWORK_TOOLS_BASE_URL: tools.baseUrl,
    KNOWWORK_SANDBOX_BASE_URL: sandbox.baseUrl,
    KNOWWORK_HOST_SESSION_IDLE_MINUTES: '0', // 宿主不释放会话，只看执行环境自己的空闲关闭
    KNOWWORK_HOST_SSH_IDLE_MINUTES: '0.05',
  })
  try {
    assert.equal((await host.call('POST', '/v1/sessions', { sessionId: session, tools: { token } })).status, 201)
    await host.call('POST', `/v1/sessions/${session}/messages`, { text: '跑一次管道' })
    await collectEvents(host, session, isTurnEnd, { timeoutMs: 120_000 })
    const result = gateway.requests.filter(r => r.path.endsWith('/chat/completions')).at(-1)
      .body.messages.filter(m => m.role === 'tool').map(m => String(m.content)).at(-1)
    assert.match(result, /"seen":"ping"/, result)
    await until(() => !container.helperRunning(), '空闲到时辅助程序退出', 30_000)
  } finally {
    await host.stop()
    await gateway.close()
    await tools.close()
    await sandbox.close()
    container.remove()
  }
})
