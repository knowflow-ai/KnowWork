/**
 * 空闲会话回收：空闲超时后释放（内存不再只涨不降），会话日志还在，恢复打开后历史照旧；
 * 还有事件流订阅者、没停下来、有待决审批或提问、名下还有在跑的后台任务时不释放。
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createIdleTracker, idleMsOf, releasable, sweepMsOf } from '../plugins/dsh-host/src/idle-sessions.mjs'
import { startHost, collectEvents, isTurnEnd } from './support/harness.mjs'
import { startOpenAiStub } from './support/openai-stub.mjs'
import { startSandboxStub } from './support/sandbox-stub.mjs'
import { startToolsStub } from './support/tools-stub.mjs'

const IDLE_MINUTES = String(300 / 60_000) // 300 毫秒
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))

test('空闲时长：默认 30 分钟，0 不回收，非法的启动时就报错；检查间隔取一半并夹在 50 毫秒到 1 分钟', () => {
  assert.equal(idleMsOf(undefined), 30 * 60_000)
  assert.equal(idleMsOf('0'), 0)
  assert.equal(idleMsOf('0.5'), 30_000)
  assert.throws(() => idleMsOf('-1'), /不小于 0/)
  assert.throws(() => idleMsOf('abc'), /不小于 0/)
  assert.equal(sweepMsOf(30 * 60_000), 60_000)
  assert.equal(sweepMsOf(300), 150)
  assert.equal(sweepMsOf(10), 50)
})

test('空闲判断：最近一次活动起算；关掉回收时永远不到期', () => {
  let now = 0
  const tracker = createIdleTracker(1000, () => now)
  tracker.touch('s1')
  now = 999
  assert.equal(tracker.due('s1'), false)
  now = 1000
  assert.equal(tracker.due('s1'), true)
  tracker.touch('s1')
  assert.equal(tracker.due('s1'), false)
  assert.equal(tracker.due('never-touched'), false)
  const off = createIdleTracker(0, () => now)
  off.touch('s1')
  now = 10 ** 9
  assert.equal(off.due('s1'), false)
})

test('能不能释放：空闲、没有待决审批与提问、没有订阅者、没有在跑的后台任务；查任务出错当作不能', () => {
  const agent = { status: 'idle', id: 's1' }
  const state = { approvals: new Map(), questions: new Map(), subscribers: new Set() }
  const jobs = list => ({ list: () => list })
  assert.equal(releasable(agent, state, jobs([{ status: 'completed' }, { status: 'killed' }])), true)
  assert.equal(releasable(agent, undefined, undefined), true)
  assert.equal(releasable({ ...agent, status: 'running' }, state, undefined), false)
  assert.equal(releasable(agent, { ...state, approvals: new Map([['a', {}]]) }, undefined), false)
  assert.equal(releasable(agent, { ...state, questions: new Map([['q', {}]]) }, undefined), false)
  assert.equal(releasable(agent, { ...state, subscribers: new Set([{}]) }, undefined), false)
  assert.equal(releasable(agent, state, jobs([{ status: 'running' }])), false, '后台任务还在跑')
  assert.equal(releasable(agent, state, jobs([{ status: 'stopping' }])), false)
  assert.equal(releasable(agent, state, { list: () => { throw new Error('boom') } }), false)
})

test('空闲超时后释放；恢复打开后历史照旧，业务服务走的就是这条路', async () => {
  const host = await startHost({ sequence: ['success'], successText: '好的' }, { KNOWWORK_HOST_SESSION_IDLE_MINUTES: IDLE_MINUTES })
  try {
    await host.call('POST', '/v1/sessions', { sessionId: 'idle-1' })
    await host.call('POST', '/v1/sessions/idle-1/messages', { text: '第一句：记住暗号 蓝鲸' })
    await collectEvents(host, 'idle-1', isTurnEnd)
    let status = 200
    for (let i = 0; i < 40 && status === 200; i += 1) {
      await sleep(100)
      status = (await host.call('GET', '/v1/sessions/idle-1')).status
    }
    assert.equal(status, 404, '空闲超时后会话应已释放')
    assert.match(host.stderr() + host.stdout(), /idle-1 idle for/)

    const resumed = await host.call('POST', '/v1/sessions', { sessionId: 'idle-1', resume: true, policy: {} })
    assert.equal(resumed.status, 201, JSON.stringify(resumed.body))
    await host.call('POST', '/v1/sessions/idle-1/messages', { text: '第二句' })
    await collectEvents(host, 'idle-1', isTurnEnd)
    const last = JSON.stringify(host.llm.requests.at(-1).body?.messages ?? [])
    assert.ok(last.includes('蓝鲸') && last.includes('第二句'), '恢复后的请求里应带着释放前的历史')
  } finally {
    await host.stop()
  }
})

test('还有事件流订阅者（业务服务的事件泵没退出）时不释放', async () => {
  const host = await startHost({ sequence: ['success'] }, { KNOWWORK_HOST_SESSION_IDLE_MINUTES: IDLE_MINUTES })
  const controller = new AbortController()
  try {
    await host.call('POST', '/v1/sessions', { sessionId: 'idle-2' })
    const subscribed = await fetch(`${host.base}/v1/sessions/idle-2/events?after=0`, {
      headers: { authorization: `Bearer ${host.token}` },
      signal: controller.signal,
    })
    assert.equal(subscribed.status, 200)
    await sleep(1000)
    assert.equal((await host.call('GET', '/v1/sessions/idle-2')).status, 200, '有订阅者时被释放了')
  } finally {
    controller.abort()
    await host.stop()
  }
})

test('名下还有在跑的后台命令时不释放（释放会把它杀掉），跑完、模型收到完成通知后才释放', async () => {
  const TOKEN = 'idle-bg-token-'.padEnd(40, 'b')
  const gateway = await startOpenAiStub({ script: [
    { tool: 'bash', args: { command: 'sleep 3 && echo 跑完了', description: '后台跑三秒', run_in_background: true } },
    { text: '已经放到后台跑了' },
    { text: '后台命令跑完了' },
  ] })
  const tools = await startToolsStub({ toolsByToken: { [TOKEN]: [] } })
  const sandbox = await startSandboxStub({ sessionsByToken: { [TOKEN]: 'idle-3' } })
  const host = await startHost({ sequence: ['success'] }, {
    KNOWWORK_MODEL_PROVIDER: 'knowwork-gateway',
    KNOWWORK_MODEL: 'kw-chat',
    KNOWWORK_MODEL_BASE_URL: gateway.baseURL,
    KNOWWORK_MODEL_TOKEN: 'gw-token-idle-0001-aaaaaaaaaa',
    KNOWWORK_TOOLS_BASE_URL: tools.baseUrl,
    KNOWWORK_SANDBOX_BASE_URL: sandbox.baseUrl,
    KNOWWORK_HOST_SESSION_IDLE_MINUTES: IDLE_MINUTES,
  })
  try {
    await host.call('POST', '/v1/sessions', { sessionId: 'idle-3', tools: { token: TOKEN } })
    await host.call('POST', '/v1/sessions/idle-3/messages', { text: '后台跑个命令' })
    await collectEvents(host, 'idle-3', isTurnEnd)
    await sleep(1500)
    assert.equal((await host.call('GET', '/v1/sessions/idle-3')).status, 200, '后台命令还在跑时会话被释放了')
    let status = 200
    for (let i = 0; i < 100 && status === 200; i += 1) {
      await sleep(100)
      status = (await host.call('GET', '/v1/sessions/idle-3')).status
    }
    assert.equal(status, 404, '后台命令跑完、空闲超时后应释放')
    const seen = JSON.stringify(gateway.requests.map(r => r.body?.messages ?? []))
    assert.ok(seen.includes('跑完了'), '后台命令没跑完就被结束了，模型没收到它的输出')
  } finally {
    await host.stop()
    await gateway.close()
    await tools.close()
    await sandbox.close()
  }
})
