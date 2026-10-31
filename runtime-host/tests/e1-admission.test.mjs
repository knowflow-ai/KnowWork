/**
 * E1 准入测试：用真实 DSH 组合加官方模拟模型服务，验证 KnowWork 依赖的扩展点。
 * 每个用例一个独立宿主进程与 DSH_HOME。断言一律落到实际效果（模型请求内容、事件内容），
 * 不只看回合结束——回合失败也会发 turn/end。
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { startHost, collectEvents, isTurnEnd } from './support/harness.mjs'

const sessionEvents = frames => frames.filter(f => f.kind === 'session/event').map(f => f.data)
const toolNamesOf = request => (request.body?.tools ?? []).map(t => t.name)
const messagesText = request => JSON.stringify(request.body?.messages ?? [])
const lastCursor = frames => frames.at(-1)?.cursor ?? 0
const isRunning = f => f.kind === 'agent/status' && f.data.status === 'running'
const turnEndReason = frames => sessionEvents(frames).find(e => e.type === 'turn/end')?.data?.reason?.kind

test('生命周期：执行中取消、取消后马上再发、释放后恢复再发', async () => {
  const host = await startHost({ sequence: ['slow_success', 'success'], chunkDelayMs: 200, successText: '第二轮的回答内容' })
  try {
    assert.equal((await host.call('POST', '/v1/sessions', { sessionId: 'life-1' })).status, 201)
    await host.call('POST', '/v1/sessions/life-1/messages', { text: '第一轮' })
    const first = await collectEvents(host, 'life-1', isRunning)
    const cancelled = await host.call('POST', '/v1/sessions/life-1/cancel')
    assert.equal(cancelled.status, 200)
    assert.equal(cancelled.body.settled, true, `取消未在 5 秒内停稳：${JSON.stringify(cancelled.body)}`)
    const afterCancel = await collectEvents(host, 'life-1', isTurnEnd, { after: lastCursor(first) })

    await host.call('POST', '/v1/sessions/life-1/messages', { text: '第二轮' })
    const second = await collectEvents(host, 'life-1', isTurnEnd, { after: lastCursor(afterCancel) })
    assert.equal(turnEndReason(second), 'completed', `第二轮没有正常完成：${JSON.stringify(sessionEvents(second).at(-1))}`)
    assert.ok(JSON.stringify(sessionEvents(second)).includes('第二轮的回答内容'), '第二轮回答没有出现')

    assert.equal((await host.call('DELETE', '/v1/sessions/life-1')).status, 200)
    const resumed = await host.call('POST', '/v1/sessions', { sessionId: 'life-1', resume: true, policy: {} })
    assert.equal(resumed.status, 201, JSON.stringify(resumed.body))
    await host.call('POST', '/v1/sessions/life-1/messages', { text: '恢复后第三轮' })
    const third = await collectEvents(host, 'life-1', isTurnEnd)
    assert.equal(turnEndReason(third), 'completed')
    const last = host.llm.requests.at(-1)
    assert.ok(messagesText(last).includes('恢复后第三轮'), '最后一次请求不是恢复后这一轮发出的')
    assert.ok(messagesText(last).includes('第二轮'), '恢复后的请求里没有之前的历史')
  } finally {
    await host.stop()
  }
})

test('恢复会话必须重新给出策略，宿主不替调用方记住', async () => {
  const host = await startHost({ sequence: ['success'] })
  try {
    await host.call('POST', '/v1/sessions', { sessionId: 'res-1', policy: { deny: ['kw_echo'] } })
    await host.call('DELETE', '/v1/sessions/res-1')
    const missing = await host.call('POST', '/v1/sessions', { sessionId: 'res-1', resume: true })
    assert.equal(missing.status, 400)
    assert.equal(missing.body.error.code, 'policy_required')
  } finally {
    await host.stop()
  }
})

test('会话级工具收窄：被收窄的工具不出现在该会话的模型请求里，其他会话不受影响', async () => {
  const host = await startHost({ sequence: ['success'] })
  try {
    await host.call('POST', '/v1/sessions', { sessionId: 'narrow', policy: { deny: ['kw_echo'] } })
    await host.call('POST', '/v1/sessions', { sessionId: 'open' })
    await host.call('POST', '/v1/sessions/narrow/messages', { text: '收窄会话' })
    await collectEvents(host, 'narrow', isTurnEnd)
    const narrowRequest = host.llm.requests.at(-1)
    await host.call('POST', '/v1/sessions/open/messages', { text: '普通会话' })
    await collectEvents(host, 'open', isTurnEnd)
    const openRequest = host.llm.requests.at(-1)
    assert.ok(messagesText(narrowRequest).includes('收窄会话'))
    assert.ok(messagesText(openRequest).includes('普通会话'))
    assert.ok(!toolNamesOf(narrowRequest).includes('kw_echo'), `收窄会话仍看得到 kw_echo：${toolNamesOf(narrowRequest)}`)
    assert.ok(toolNamesOf(openRequest).includes('kw_echo'), `普通会话看不到 kw_echo：${toolNamesOf(openRequest)}`)
  } finally {
    await host.stop()
  }
})

test('审批由外部决定：批准后工具执行；拒绝后工具不执行但回合继续', async () => {
  for (const outcome of ['allowed-once', 'rejected']) {
    const host = await startHost({ sequence: ['tool_call_success', 'success'], toolName: 'kw_write', toolArguments: JSON.stringify({ text: '合同台账' }) })
    try {
      await host.call('POST', '/v1/sessions', { sessionId: 'appr' })
      await host.call('POST', '/v1/sessions/appr/messages', { text: '写入台账' })
      const asked = await collectEvents(host, 'appr', f => f.kind === 'approval/requested')
      const pending = await host.call('GET', '/v1/sessions/appr/approvals')
      assert.equal(pending.body.length, 1)
      assert.equal(pending.body[0].toolName, 'kw_write')
      assert.equal((await host.call('POST', `/v1/sessions/appr/approvals/${pending.body[0].id}`, { outcome })).status, 200)
      const rest = await collectEvents(host, 'appr', isTurnEnd, { after: lastCursor(asked) })
      assert.equal(rest.find(f => f.kind === 'approval/resolved')?.data.outcome, outcome)
      assert.equal(host.llm.requests.length, 2, '工具结果之后应该还有一次模型请求')
      const followup = messagesText(host.llm.requests[1])
      if (outcome === 'allowed-once') assert.ok(followup.includes('written: 合同台账'), '批准后工具没有执行')
      else assert.ok(!followup.includes('written: 合同台账'), '拒绝后工具仍然执行了')
      assert.equal(turnEndReason(rest), 'completed')
    } finally {
      await host.stop()
    }
  }
})

test('释放会话时，待决审批结算为取消，释放不被审批卡住', async () => {
  const host = await startHost({ sequence: ['tool_call_success', 'success'], toolName: 'kw_write', toolArguments: JSON.stringify({ text: 'x' }) })
  try {
    await host.call('POST', '/v1/sessions', { sessionId: 'appr-drop' })
    await host.call('POST', '/v1/sessions/appr-drop/messages', { text: '写入' })
    await collectEvents(host, 'appr-drop', f => f.kind === 'approval/requested')
    const started = Date.now()
    const disposed = await host.call('DELETE', '/v1/sessions/appr-drop')
    assert.equal(disposed.status, 200)
    assert.ok(Date.now() - started < 5000, '释放被待决审批卡住')
    assert.equal((await host.call('GET', '/v1/sessions/appr-drop/approvals')).status, 404)
  } finally {
    await host.stop()
  }
})

test('技能门：没调用收尾工具就想结束时被 steer 回去，调用后放行', async () => {
  const host = await startHost({
    sequence: ['success', 'tool_call_success', 'success'],
    toolName: 'kw_finalize',
    toolArguments: JSON.stringify({ summary: '12 份合同已抽取' }),
  })
  try {
    await host.call('POST', '/v1/sessions', { sessionId: 'gate', policy: { gate: { requiredTool: 'kw_finalize' } } })
    await host.call('POST', '/v1/sessions/gate/messages', { text: '抽取合同字段' })
    const frames = await collectEvents(host, 'gate', isTurnEnd)
    const kinds = frames.map(f => f.kind)
    assert.ok(kinds.includes('skill-gate/nudged'), '没有触发补救')
    assert.ok(kinds.includes('skill-gate/passed'), `没有放行：${kinds.join(',')}`)
    assert.ok(!kinds.includes('skill-gate/failed'))
    assert.equal(host.llm.requests.length, 3)
    assert.ok(messagesText(host.llm.requests[1]).includes('完成判据'), '第二次请求里没有完成判据的提示')
    assert.equal(turnEndReason(frames), 'completed')
  } finally {
    await host.stop()
  }
})

test('技能门：补救次数用完后放行结束并发出失败事件', async () => {
  const host = await startHost({ sequence: ['success'] })
  try {
    await host.call('POST', '/v1/sessions', { sessionId: 'gate2', policy: { gate: { requiredTool: 'kw_finalize', maxNudges: 2 } } })
    await host.call('POST', '/v1/sessions/gate2/messages', { text: '直接回答' })
    const frames = await collectEvents(host, 'gate2', isTurnEnd)
    assert.equal(frames.filter(f => f.kind === 'skill-gate/nudged').length, 2)
    const failedAt = frames.findIndex(f => f.kind === 'skill-gate/failed')
    assert.ok(failedAt >= 0, '没有失败事件')
    assert.ok(frames.findIndex(isTurnEnd) > failedAt, '失败事件之后没有结束回合')
    assert.equal(host.llm.requests.length, 3)
  } finally {
    await host.stop()
  }
})

test('技能门：上一轮的成功不算到下一轮', async () => {
  const host = await startHost({
    sequence: ['tool_call_success', 'success', 'success'],
    toolName: 'kw_finalize',
    toolArguments: JSON.stringify({ summary: '第一轮' }),
  })
  try {
    await host.call('POST', '/v1/sessions', { sessionId: 'gate3', policy: { gate: { requiredTool: 'kw_finalize', maxNudges: 0 } } })
    await host.call('POST', '/v1/sessions/gate3/messages', { text: '第一轮' })
    const first = await collectEvents(host, 'gate3', isTurnEnd)
    assert.ok(first.some(f => f.kind === 'skill-gate/passed'), '第一轮应当放行')
    await host.call('POST', '/v1/sessions/gate3/messages', { text: '第二轮不调用收尾工具' })
    const second = await collectEvents(host, 'gate3', isTurnEnd, { after: lastCursor(first) })
    assert.ok(second.some(f => f.kind === 'skill-gate/failed'), '第二轮没调用收尾工具却被放行了')
  } finally {
    await host.stop()
  }
})

test('插话：执行中 steer 的内容出现在后续模型请求里', async () => {
  const host = await startHost({ sequence: ['slow_success', 'success'], chunkDelayMs: 150, successText: '正在整理一份比较长的清单，请稍候' })
  try {
    await host.call('POST', '/v1/sessions', { sessionId: 'steer' })
    await host.call('POST', '/v1/sessions/steer/messages', { text: '列出所有合同' })
    const running = await collectEvents(host, 'steer', isRunning)
    assert.equal((await host.call('POST', '/v1/sessions/steer/messages', { text: '补充：只要采购合同', mode: 'steer' })).status, 202)
    await collectEvents(host, 'steer', isTurnEnd, { after: lastCursor(running) })
    assert.ok(host.llm.requests.length >= 2)
    assert.ok(host.llm.requests.slice(1).some(r => messagesText(r).includes('补充：只要采购合同')), '后续请求里没有插话内容')
  } finally {
    await host.stop()
  }
})

test('同一会话并发打开：只有一个成功，其余 409，成功的会话不受影响', async () => {
  const host = await startHost({ sequence: ['success'], successText: '并发后的回答' })
  try {
    const results = await Promise.all(Array.from({ length: 5 }, () => host.call('POST', '/v1/sessions', { sessionId: 'race' })))
    const statuses = results.map(r => r.status).sort()
    assert.deepEqual(statuses, [201, 409, 409, 409, 409], JSON.stringify(results.map(r => r.body)))
    await host.call('POST', '/v1/sessions/race/messages', { text: '还能用吗' })
    const frames = await collectEvents(host, 'race', isTurnEnd)
    assert.ok(JSON.stringify(sessionEvents(frames)).includes('并发后的回答'))
  } finally {
    await host.stop()
  }
})

test('鉴权与输入校验', async () => {
  const host = await startHost({ sequence: ['success'] })
  try {
    const sameLength = 'x'.repeat(host.token.length)
    for (const authorization of [undefined, 'Bearer wrong', `Bearer ${sameLength}`]) {
      for (const [method, path] of [['POST', '/v1/sessions'], ['GET', '/v1/sessions/a/events'], ['POST', '/v1/sessions/a/approvals/b']]) {
        const res = await fetch(`${host.base}${path}`, { method, headers: authorization ? { authorization } : {}, body: method === 'POST' ? '{}' : undefined })
        assert.equal(res.status, 401, `${method} ${path} 带 ${authorization} 没有被拒绝`)
      }
    }
    const big = await fetch(`${host.base}/v1/sessions`, {
      method: 'POST',
      headers: { authorization: `Bearer ${host.token}` },
      body: JSON.stringify({ sessionId: 'big', pad: 'x'.repeat(1024 * 1024 + 10) }),
    })
    assert.equal(big.status, 413)
    assert.equal((await host.call('POST', '/v1/sessions', { sessionId: '../etc' })).status, 400)
    assert.equal((await host.call('GET', '/v1/sessions/%E0%A4%A/events')).status, 400)
  } finally {
    await host.stop()
  }
})

test('stdin 关闭时宿主仍持续服务（服务端部署形态）', async () => {
  const host = await startHost({ sequence: ['success'] }, {}, { stdin: 'ignore' })
  try {
    await new Promise(r => setTimeout(r, 1500))
    assert.equal(host.child.exitCode, null, '宿主在 stdin 关闭后退出了')
    const health = await fetch(`${host.base}/health`)
    assert.equal(health.status, 200)
    // 版本号与锁定的 DSH 版本一致，系统信息页靠它显示
    assert.equal((await health.json()).dsh, '0.2.2-alpha.2')
  } finally {
    await host.stop()
  }
})
