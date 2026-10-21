/**
 * E1 剩余项 4：宿主在等待审批时重启（正常关闭与崩溃两种），会话恢复后的行为。
 * 安全底线：等待审批的操作在重启后绝不能自行执行。
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { startHost, collectEvents, isTurnEnd } from './support/harness.mjs'

const sessionEvents = frames => frames.filter(f => f.kind === 'session/event').map(f => f.data)

for (const signal of ['SIGTERM', 'SIGKILL']) {
  test(`等待审批时宿主${signal === 'SIGKILL' ? '崩溃' : '正常关闭'}：恢复后审批不复活、写入不执行，会话可继续`, async () => {
    const mock = { sequence: ['tool_call_success', 'success'], toolName: 'kw_write', toolArguments: JSON.stringify({ text: '敏感写入' }) }
    const first = await startHost(mock)
    let second
    try {
      await first.call('POST', '/v1/sessions', { sessionId: 'restart-1' })
      await first.call('POST', '/v1/sessions/restart-1/messages', { text: '写入' })
      await collectEvents(first, 'restart-1', f => f.kind === 'approval/requested')
      await first.stop({ keepHome: true, signal })

      second = await startHost({ sequence: ['success'], successText: '重启后的回答' }, {}, { home: first.home })
      const resumed = await second.call('POST', '/v1/sessions', { sessionId: 'restart-1', resume: true, policy: {} })
      assert.equal(resumed.status, 201, JSON.stringify(resumed.body))
      const pending = await second.call('GET', '/v1/sessions/restart-1/approvals')
      assert.deepEqual(pending.body, [], '重启后出现了待决审批')

      await second.call('POST', '/v1/sessions/restart-1/messages', { text: '重启后继续' })
      const frames = await collectEvents(second, 'restart-1', isTurnEnd)
      assert.equal(sessionEvents(frames).find(e => e.type === 'turn/end')?.data?.reason?.kind, 'completed')
      const all = JSON.stringify(second.llm.requests.map(r => r.body?.messages ?? []))
      assert.ok(!all.includes('written: 敏感写入'), '等待审批的写入在重启后被执行了')
      assert.ok(all.includes('重启后继续'))
    } finally {
      await second?.stop()
    }
  })
}
