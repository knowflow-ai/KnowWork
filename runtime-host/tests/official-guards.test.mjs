/**
 * 照官方完整版补挂的两个官方插件：重复调用提醒（相同参数连调同一工具时提醒模型换办法）、工具结果裁剪
 * （压缩触发时先裁超长的工具结果）。裁剪要等到上下文压力才运行，这里只确认它随宿主挂上；提醒看实际行为。
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { startHost, collectEvents, isTurnEnd } from './support/harness.mjs'

test('模型用相同参数连调同一个工具三次，下一次请求里带上官方的提醒', async () => {
  const host = await startHost({
    sequence: ['tool_call_success', 'tool_call_success', 'tool_call_success', 'success'],
    toolName: 'kw_echo',
    toolArguments: JSON.stringify({ text: '同一句' }),
  })
  try {
    await host.call('POST', '/v1/sessions', { sessionId: 'guard-1' })
    await host.call('POST', '/v1/sessions/guard-1/messages', { text: '反复做同一件事' })
    await collectEvents(host, 'guard-1', isTurnEnd)
    const last = JSON.stringify(host.llm.requests.at(-1).body?.messages ?? [])
    assert.ok(last.includes('You are repeating the exact same tool call'), '第三次重复之后没有提醒')
  } finally {
    await host.stop()
  }
})

test('工具结果裁剪与重复提醒挂在宿主配置里，配置照官方完整版', () => {
  const patch = readFileSync(new URL('../plugins/host-bundle/cordis.patch.yml', import.meta.url), 'utf8')
  assert.match(patch, /name: '@deepseek-ai\/dsh-compaction-tool-result-pruner'\n\s+config:\n\s+thresholdChars: 8192/)
  assert.match(patch, /name: '@deepseek-ai\/dsh-repeat-tool-reminder'/)
})
