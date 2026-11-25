/**
 * 业务服务管专属宿主用的两个接口。
 *
 * 宿主的插件清单（GET /v1/plugins，业务服务试装上传的插件时读）：照官方插件清单读加载器，激活的标 active；
 * 模块导入不了的没有 fiber（阶段为 null，原因只在宿主日志里，业务服务试装时从日志里找）；另给出此刻登记了的工具名。
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { startHost } from './support/harness.mjs'

const BROKEN = join(dirname(fileURLToPath(import.meta.url)), 'overlays', 'broken-plugin.yml')

test('插件清单：激活的、导入不了的、登记了的工具', async () => {
  const host = await startHost({ sequence: ['success'] }, {}, { patches: [BROKEN] })
  try {
    const listed = await host.call('GET', '/v1/plugins')
    assert.equal(listed.status, 200, JSON.stringify(listed.body))
    const byModule = new Map(listed.body.entries.map(entry => [entry.moduleName, entry]))
    assert.equal(byModule.get('@knowwork/dsh-host/test-tools')?.fiberPhase, 'active')
    const broken = byModule.get('@knowwork/does-not-exist')
    assert.equal(broken?.enabled, true)
    assert.equal(broken?.fiberPhase, null, '导入不了的模块没有 fiber')
    assert.match(host.stdout() + host.stderr(), /does-not-exist/, '原因在宿主日志里')
    assert.ok(listed.body.tools.includes('kw_echo'), '测试工具登记了')
    assert.deepEqual(listed.body.tools, [...listed.body.tools].sort())

    const denied = await fetch(`${host.base}/v1/plugins`, { headers: { authorization: `Bearer ${'x'.repeat(40)}` } })
    assert.equal(denied.status, 401, '要宿主令牌')
  } finally {
    await host.stop()
  }
})

test('活动情况（GET /v1/activity）：开着几段会话、几段没停下、离最后一次被用到多久；续签令牌、查状态不算用到', async () => {
  const host = await startHost({ sequence: ['success'] })
  try {
    const fresh = await host.call('GET', '/v1/activity')
    assert.equal(fresh.status, 200)
    assert.deepEqual({ sessions: fresh.body.sessions, busy: fresh.body.busy }, { sessions: 0, busy: 0 })
    const opened = await host.call('POST', '/v1/sessions', { sessionId: 'activity-1' })
    assert.ok(opened.status < 300, JSON.stringify(opened.body))
    const after = (await host.call('GET', '/v1/activity')).body
    assert.equal(after.sessions, 1)
    assert.equal(after.busy, 0, '刚打开、没在跑的会话不算忙')
    await new Promise(resolve => setTimeout(resolve, 60))
    await host.call('PUT', '/v1/sessions/activity-1/tool-credential', { token: 'activity-token'.padEnd(40, 'b') })
    await host.call('GET', '/v1/sessions/activity-1')
    const quiet = (await host.call('GET', '/v1/activity')).body
    assert.ok(quiet.idleMs >= 50, '续签令牌、查状态不算用到')
  } finally {
    await host.stop()
  }
})
